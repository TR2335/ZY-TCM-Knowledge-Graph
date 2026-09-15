# ===================================================================
# 文件：backend/tests/pipeline/test_download.py（pipeline 层单测 · 下载器 C1）
# 用途：HTTP 全 mock 验证下载器——manifest 登记、幂等跳过、--force 覆盖、legacy 复制
# 核心保障：下载器可重复执行（幂等），产物带 sha256 校验（可审计）
# ===================================================================

"""C1 下载器单测：manifest 登记、幂等跳过、legacy 复制（HTTP 全 mock）。"""

import hashlib  # 标准库：计算 sha256（断言 manifest 记录用）
import json  # 标准库：读 manifest.jsonl
from pathlib import Path  # 标准库：tmp_path 夹具

import httpx  # 第三方：MockTransport 替换真实网络

from tcm_kg.pipeline.download import (  # 被测对象：下载器组件
    FileSpec, Manifest, SourceSpec,  # 数据结构
    _sync_legacy_dir, download_all,  # 流程函数
)


def _client(responses: dict[str, bytes]) -> httpx.Client:
    """构造对 GET url 返回固定内容的 mock client（transport 层替换）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        # 优先按查询参数 file= 匹配，其次按完整 URL 匹配
        content = responses.get(request.url.params.get("file", ""), responses.get(str(request.url)))
        if content is None:  # 未登记的 URL
            return httpx.Response(404)  # 返回 404
        return httpx.Response(200, content=content)  # 命中：200 + 固定内容

    return httpx.Client(transport=httpx.MockTransport(handler))  # 注入 mock 传输层


def test_download_writes_manifest(tmp_path: Path) -> None:
    """下载落盘 + manifest.jsonl 记录 sha256/版本/来源。"""
    spec = SourceSpec(  # 一个数据源规格
        name="symmap",  # 源名
        version="v2.0",  # 版本
        subdir="symmap",  # 存放子目录
        files=(FileSpec(url="http://x/file.xlsx?file=a.xlsx", filename="a.xlsx"),),  # 一个文件
    )
    with _client({"a.xlsx": b"hello"}) as client:  # mock：a.xlsx 返回 "hello"
        from tcm_kg.pipeline.download import _download_source  # 局部导入（单源下载）

        manifest = Manifest(tmp_path)  # manifest 管理器
        records = _download_source(client, spec, tmp_path, manifest, force=False)  # 执行下载
    assert (tmp_path / "symmap" / "a.xlsx").read_bytes() == b"hello"  # 文件落盘
    assert len(records) == 1  # 一条记录
    manifest.save()  # 保存 manifest
    saved = json.loads((tmp_path / "manifest.jsonl").read_text(encoding="utf-8"))  # 读回
    assert saved["source"] == "symmap"  # 源名正确
    assert saved["sha256"] == hashlib.sha256(b"hello").hexdigest()  # 校验和正确


def test_download_idempotent_skip(tmp_path: Path) -> None:
    """二次执行：文件未变化 → 跳过，不新增记录。"""
    spec = SourceSpec(  # 同一规格
        name="symmap",
        version="v2.0",
        subdir="symmap",
        files=(FileSpec(url="http://x/file.xlsx?file=a.xlsx", filename="a.xlsx"),),
    )
    from tcm_kg.pipeline.download import _download_source  # 单源下载

    manifest = Manifest(tmp_path)  # manifest
    with _client({"a.xlsx": b"hello"}) as client:  # 第一次下载
        first = _download_source(client, spec, tmp_path, manifest, force=False)
    with _client({"a.xlsx": b"hello"}) as client:  # 第二次下载（内容相同）
        second = _download_source(client, spec, tmp_path, manifest, force=False)
    assert len(first) == 1  # 首次：1 条记录
    assert second == []  # 二次：跳过（幂等，内容 hash 未变）


def test_download_force_overwrites(tmp_path: Path) -> None:
    """--force：内容变化时强制重下并更新 manifest。"""
    spec = SourceSpec(  # 同一规格
        name="symmap",
        version="v2.0",
        subdir="symmap",
        files=(FileSpec(url="http://x/file.xlsx?file=a.xlsx", filename="a.xlsx"),),
    )
    from tcm_kg.pipeline.download import _download_source  # 单源下载

    manifest = Manifest(tmp_path)  # manifest
    with _client({"a.xlsx": b"hello"}) as client:  # 先下 "hello"
        _download_source(client, spec, tmp_path, manifest, force=False)
    with _client({"a.xlsx": b"changed!"}) as client:  # 内容已变
        records = _download_source(client, spec, tmp_path, manifest, force=True)  # 强制重下
    assert len(records) == 1  # 重下 1 条
    assert (tmp_path / "symmap" / "a.xlsx").read_bytes() == b"changed!"  # 新内容覆盖


def test_legacy_copy(tmp_path: Path) -> None:
    """C1.6：旧项目 txt 复制到 legacy/{zhongyao,fangji}/ 并登记 manifest。"""
    src = tmp_path / "crawler"  # 旧项目爬虫目录
    (src / "中药").mkdir(parents=True)  # 中药子目录
    (src / "方剂").mkdir(parents=True)  # 方剂子目录
    (src / "中药" / "三七.txt").write_text("三七内容", encoding="utf-8")  # 一味药 txt
    (src / "方剂" / "四物汤.txt").write_text("四物汤内容", encoding="utf-8")  # 一首方 txt

    raw = tmp_path / "raw"  # 目标 raw 目录
    manifest = Manifest(raw)  # manifest
    copied = _sync_legacy_dir(src, raw, manifest, force=False)  # 执行复制
    assert copied == 2  # 复制 2 个文件
    assert (raw / "legacy" / "zhongyao" / "三七.txt").read_text(encoding="utf-8") == "三七内容"  # 中文目录名转拼音
    assert (raw / "legacy" / "fangji" / "四物汤.txt").exists()  # 方剂也复制
    # 原文件保留（旧项目只读）
    assert (src / "中药" / "三七.txt").exists()  # 不删源

    # 幂等：再跑一遍不重复复制
    again = _sync_legacy_dir(src, raw, manifest, force=False)  # 二次执行
    assert again == 0  # 零复制（已同步）


def test_download_all_stats(tmp_path: Path) -> None:
    """download_all 汇总统计 + legacy 目录缺失不报错。"""
    spec = SourceSpec(  # 一个源
        name="symmap",
        version="v2.0",
        subdir="symmap",
        files=(FileSpec(url="http://x/file.xlsx?file=a.xlsx", filename="a.xlsx"),),
    )

    stats = download_all(  # 批量下载入口
        (spec,),  # 源元组
        tmp_path,  # raw 目录
        legacy_src=None,  # 无 legacy 目录（不报错）
        client_factory=lambda: _client({"a.xlsx": b"data"}),  # 注入 mock client 工厂
    )
    assert stats["downloaded"] == 1  # 下载 1 个
    assert stats["legacy_copied"] == 0  # legacy 复制 0 个
    # manifest 已保存且可再次读入
    assert Manifest(tmp_path).records  # 记录非空（持久化成功）
