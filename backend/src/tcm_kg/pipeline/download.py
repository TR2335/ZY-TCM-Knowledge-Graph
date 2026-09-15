# ====================================================================
# 文件：download.py
# 用途：数据管线第一阶段（C1 冷启动）——从 5 个数据源拉取原始数据到 data/raw/ 目录
# 所属层级：tcm_kg.pipeline 下载层（离线管线 Phase C 第 1 步）
# 核心功能：
#   1. 批量下载 5 个数据源（SymMap/BATMAN-TCM/nmm/OpenKG/GB-T 15657）的原始文件
#   2. manifest.jsonl 登记每文件的 source/version/sha256/size/downloaded_at
#   3. 幂等重跑：sha256 与 manifest 一致则跳过（--force 强制重下）
#   4. legacy 源（旧项目已爬 txt）走本地目录复制，不走 HTTP
#   5. 失败自动重试 3 次，指数退避
# 如何使用：
#   from tcm_kg.pipeline.download import download_all
#   stats = download_all(sources, raw_dir, legacy_src=..., force=False)
#   # 或经 CLI：python -m tcm_kg.pipeline download --all
# ====================================================================
"""C1 冷启动数据获取：批量下载 + manifest 登记 + 幂等重跑。

设计要点（MASTER_PLAN C1）：
- 每个数据源注册为 SourceSpec（名称/版本/文件清单/子目录）；
- 下载走 httpx 流式写盘，简单重试（3 次退避），不引额外依赖；
- manifest.jsonl 每文件一行：source/version/filename/url/sha256/size_bytes/downloaded_at；
- 幂等：目标已存在且 sha256 与 manifest 一致则跳过（--force 强制重下）；
- legacy 源为本地目录复制（旧项目已爬 txt），不走 HTTP。

版权注意（A1 §3.3）：CC BY-NC 源（BATMAN-TCM 等）仅学术用途，
manifest 记录 URL 与版本以备 Source 节点登记引用义务。
"""


# download.py 就是项目的数据"搬运工"——从互联网和本地旧项目拉取所有原始数据文件，
# 用 manifest.jsonl 记账，支持断点续传（幂等跳过已下载的），是整个知识图谱构建流水线的第 0 步。

import hashlib  # 计算 sha256（幂等判定 + manifest 登记）
import json  # manifest.jsonl 记录的序列化/反序列化
import shutil  # legacy 源文件复制
import time  # 下载重试退避等待
from collections.abc import Callable  # client_factory 参数类型注解
from dataclasses import dataclass, field  # FileSpec/SourceSpec 数据类定义
from datetime import UTC, datetime  # downloaded_at 时间戳（UTC）
from pathlib import Path  # 文件/目录路径处理

import httpx  # HTTP 流式下载客户端

from tcm_kg.core.logging import get_logger  # 项目统一结构化日志

logger = get_logger(__name__)  # 模块级 logger

_MANIFEST_NAME = "manifest.jsonl"  # manifest 文件名（raw_dir 下）
_RETRY_TIMES = 3  # 下载失败重试次数（3 次足够覆盖瞬时网络故障）
_RETRY_BACKOFF_SECONDS = 2.0  # 重试退避基数（秒），按次数线性放大
# 部分站点（如 data.openkg.cn）拒绝非浏览器 UA，统一使用浏览器 UA
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass(frozen=True, kw_only=True)
class FileSpec:
    """单个待获取文件。"""

    url: str  # 下载直链
    filename: str  # 落盘文件名（含扩展名）


# 单个冷启动数据源规格（名称/版本/子目录/文件清单）
@dataclass(frozen=True, kw_only=True)
class SourceSpec:
    """一个冷启动数据源。"""

    name: str  # manifest 中的 source 标识，如 symmap
    version: str  # 数据源版本号（登记入 manifest）
    subdir: str  # data/raw/<subdir>/
    files: tuple[FileSpec, ...] = field(default=())  # 该源文件清单，默认为空


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()  # 初始化 sha256 摘要器
    with path.open("rb") as fh:  # 二进制只读打开
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):  # 每次 1MB 分块读取
            digest.update(chunk)  # 逐块累积更新摘要（大文件不占内存）
    return digest.hexdigest()  # 返回十六进制摘要串


class Manifest:
    """manifest.jsonl 读写：每行一个文件记录。"""

    def __init__(self, raw_dir: Path) -> None:
        self.path = raw_dir / _MANIFEST_NAME  # manifest 文件路径
        self.records: dict[str, dict[str, object]] = {}  # filename → 记录 的内存映射
        if self.path.exists():  # 已有 manifest 则加载历史记录
            for line in self.path.read_text(encoding="utf-8").splitlines():  # 逐行读取
                if line.strip():  # 跳过空行
                    record: dict[str, object] = json.loads(line)  # 每行一个 JSON 记录
                    self.records[str(record["filename"])] = record  # 以文件名为主键索引

    # 幂等判定：manifest 有记录且本地文件 sha256 一致 → 跳过下载
    def is_unchanged(self, subdir: str, filename: str, path: Path) -> bool:
        """幂等判定：manifest 有记录且本地文件 hash 一致。"""
        record = self.records.get(f"{subdir}/{filename}")  # 查 manifest 记录
        if record is None or not path.exists():  # 无记录或本地文件缺失
            return False  # 视为已变更，需要（重新）下载
        return record.get("sha256") == _sha256(path)  # hash 一致才视为未变更

    # 新增/更新一条文件记录到内存 manifest（需 save 落盘）
    def upsert(self, record: dict[str, object]) -> None:
        self.records[str(record["filename"])] = record  # 以文件名覆写（新增或更新）

    def save(self) -> None:
        # 逐条序列化为 JSON 行（ensure_ascii=False 保留中文可读性）
        lines = [json.dumps(r, ensure_ascii=False) for r in self.records.values()]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # 整体覆写落盘


def _download_one(
    client: httpx.Client, spec: FileSpec, dest: Path, *, retries: int = _RETRY_TIMES
) -> None:
    """流式下载单文件到 dest.tmp 再原子改名，失败自动重试。"""
    for attempt in range(1, retries + 1):  # 重试循环：第 1..retries 次尝试
        try:
            # 流式 GET（60s 超时，跟随重定向），避免大文件整体载入内存
            with client.stream("GET", spec.url, timeout=60.0, follow_redirects=True) as resp:
                resp.raise_for_status()  # 非 2xx 状态码抛异常
                tmp = dest.with_suffix(dest.suffix + ".tmp")  # 临时文件路径
                with tmp.open("wb") as fh:  # 先写临时文件
                    for chunk in resp.iter_bytes(1024 * 1024):  # 1MB 分块接收
                        fh.write(chunk)  # 逐块写盘
            tmp.replace(dest)  # 原子改名，避免留下半成品文件
            return  # 下载成功即返回
        except httpx.HTTPError as exc:  # 网络/HTTP 层异常
            if attempt == retries:  # 已用完最后一次重试机会
                raise  # 不再重试，向上抛出
            # 记录重试日志（结构化，含异常信息）
            logger.warning("download_retry", url=spec.url, attempt=attempt, error=str(exc))
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)  # 线性退避等待后重试


def _download_source(
    client: httpx.Client, source: SourceSpec, raw_dir: Path, manifest: Manifest, *, force: bool
) -> list[dict[str, object]]:
    """下载一个源的全部文件并登记 manifest；返回新增记录。"""
    out_dir = raw_dir / source.subdir  # 该源文件的存放目录
    out_dir.mkdir(parents=True, exist_ok=True)  # 递归创建目录（已存在则忽略）
    new_records: list[dict[str, object]] = []  # 本次新下载的 manifest 记录
    for spec in source.files:  # 遍历该源全部待获取文件
        dest = out_dir / spec.filename  # 目标落盘路径
        if not force and manifest.is_unchanged(source.subdir, spec.filename, dest):  # 幂等判定
            # 跳过日志（内容未变）
            logger.info("download_skip_unchanged", source=source.name, file=spec.filename)
            continue  # 内容未变，跳过下载
        logger.info("download_start", source=source.name, file=spec.filename)  # 开始下载日志
        _download_one(client, spec, dest)  # 下载单文件（含重试）
        record = {  # 构造 manifest 登记记录
            "source": source.name,  # 源标识
            "version": source.version,  # 源版本
            "filename": f"{source.subdir}/{spec.filename}",  # 相对 raw_dir 的文件名
            "url": spec.url,  # 下载来源 URL（引用义务备查）
            "sha256": _sha256(dest),  # 内容指纹（幂等判定依据）
            "size_bytes": dest.stat().st_size,  # 文件大小（字节）
            "downloaded_at": datetime.now(UTC).isoformat(),  # UTC 下载时间
        }
        manifest.upsert(record)  # 更新内存 manifest
        new_records.append(record)  # 计入本次新增
    return new_records  # 返回新增记录列表


# 旧项目已爬 txt 复制到 data/raw/legacy/{zhongyao,fangji}/（复制非移动）
# 逐文件 hash 后登记 manifest，文件数可能上千
def _sync_legacy_dir(src: Path, raw_dir: Path, manifest: Manifest, *, force: bool) -> int:
    """C1.6：旧项目已爬 txt 复制到 data/raw/legacy/{zhongyao,fangji}/。

    复制而非移动（旧项目只读保留）；文件数可能上千，逐文件 hash 后登记 manifest。
    """
    copied = 0  # 已复制文件计数
    mapping = {"中药": "zhongyao", "方剂": "fangji"}  # 中文源目录 → 英文子目录映射
    for cn_name, subdir in mapping.items():  # 逐类目录处理
        src_dir = src / cn_name  # 旧项目中的源目录
        if not src_dir.is_dir():  # 源目录不存在
            logger.warning("legacy_dir_missing", dir=str(src_dir))  # 告警提示
            continue  # 跳过该类，继续下一类
        dest_dir = raw_dir / "legacy" / subdir  # 目标目录 data/raw/legacy/<subdir>
        dest_dir.mkdir(parents=True, exist_ok=True)  # 递归创建目标目录
        for txt in sorted(src_dir.glob("*.txt")):  # 排序遍历（保证确定性顺序）
            dest = dest_dir / txt.name  # 目标文件路径
            if not force and dest.exists() and _sha256(txt) == _sha256(dest):  # 幂等判定
                continue  # 已一致则跳过复制
            shutil.copy2(txt, dest)  # 复制并保留元数据（旧项目只读保留原件）
            manifest.upsert(  # 登记复制结果到 manifest
                {
                    "source": "legacy_crawler",  # 源标识：旧项目爬虫
                    "version": "v1",  # 旧项目版本
                    "filename": f"legacy/{subdir}/{txt.name}",  # 相对路径文件名
                    "url": f"local:{src_dir / txt.name}",  # 本地来源标记（非 HTTP）
                    "sha256": _sha256(dest),  # 内容指纹
                    "size_bytes": dest.stat().st_size,  # 文件大小（字节）
                    "downloaded_at": datetime.now(UTC).isoformat(),  # UTC 复制时间
                }
            )
            copied += 1  # 计数累加
    return copied  # 返回复制文件总数


# 执行全部源下载 + legacy 复制，返回统计（downloaded/skipped/legacy_copied）
# client_factory 供测试注入 mock（默认真实 httpx.Client + 浏览器 UA）
def download_all(
    sources: tuple[SourceSpec, ...],
    raw_dir: Path,
    *,
    legacy_src: Path | None = None,
    force: bool = False,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> dict[str, int]:
    """执行全部源下载 + legacy 复制，返回统计。

    client_factory 供测试注入 mock（默认真实 httpx.Client）。
    """
    # 未注入 client_factory 时默认真实客户端（浏览器 UA 规避站点拦截）
    factory = client_factory or (lambda: httpx.Client(headers={"User-Agent": _BROWSER_UA}))
    stats: dict[str, int] = {"downloaded": 0, "skipped": 0, "legacy_copied": 0}  # 统计初始化
    raw_dir.mkdir(parents=True, exist_ok=True)  # 确保 raw 目录存在
    manifest = Manifest(raw_dir)  # 加载/初始化 manifest
    with factory() as client:  # 建立下载客户端会话（复用连接）
        for source in sources:  # 按注册顺序逐源下载
            before = len(manifest.records)  # 下载前记录数（算 total_seen 用）
            records = _download_source(client, source, raw_dir, manifest, force=force)  # 单源下载
            stats["downloaded"] += len(records)  # 累计新下载数
            stats["skipped"] += len(source.files) - len(records)  # 累计跳过数
            if records:  # 本源有新增记录
                manifest.save()  # 立即落盘（断点续跑粒度：源级）
            logger.info(  # 记录单源完成日志
                "source_done",
                source=source.name,
                new=len(records),  # 本次新增记录数
                total_seen=len(manifest.records) - before,  # 累计见过的记录数
            )
    if legacy_src is not None:  # 指定了旧项目源目录
        # 复制 legacy 目录并登记 manifest
        stats["legacy_copied"] = _sync_legacy_dir(legacy_src, raw_dir, manifest, force=force)
        manifest.save()  # legacy 记录落盘
    return stats  # 返回下载/跳过/复制统计
