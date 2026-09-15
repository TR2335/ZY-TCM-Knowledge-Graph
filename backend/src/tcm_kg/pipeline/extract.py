# ====================================================================
# 文件：extract.py
# 用途：从非结构化文本（典籍/文献/legacy 百科 txt）抽取中医药实体（LLM 辅助）
# 所属层级：tcm_kg.pipeline 抽取层（离线管线 Phase C 第 2.5 步，C4）
# 核心功能：
#   1. C4.1 prompt 构建：system 注入抽取模型 JSON Schema（Pydantic 反射）+ 领域规则
#   2. C4.2 Extractor：StructuredLLM 原生 structured output → HerbExtract/FormulaExtract
#   3. C4.3 并发：asyncio + Semaphore（默认 5）
#   4. C4.4 断点续跑：checkpoint 记录入参 sha256，重跑跳过已处理文件
#   5. C4.5 失败处理：重试 2 次 → failures.jsonl
#   6. C4.6 输出 data/extracted/legacy_{herb,formula}.jsonl（envelope + provenance llm_extract 0.7）
#   7. C4.7 统计报告：成功率/字段覆盖率/耗时/token
#   8. 转换语义：HerbExtract → Herb + Effect×n + HAS_EFFECT×n
#      FormulaExtract → Formula + Herb(仅 NodeRef) + COMPOSED_OF + Pattern + TREATS_PATTERN + ...
# 如何使用：
#   from tcm_kg.pipeline.extract import extract_cli
#   stats = await extract_cli(raw_dir, out_dir, llm, kinds=["herb","formula"])
# ====================================================================
"""C4 LLM 结构化抽取：legacy txt（中药/方剂百科）→ 本体 envelope。

流程（MASTER_PLAN C4.1-C4.7）：
- C4.1 prompt：system 注入抽取模型 JSON Schema（Pydantic 反射）+ 领域规则；
- C4.2 Extractor：StructuredLLM（原生 structured output）→ HerbExtract/FormulaExtract；
- C4.3 并发：asyncio + Semaphore（settings.pipeline.llm_concurrency，默认 5）；
- C4.4 断点续跑：checkpoint 记录入参 sha256，重跑跳过已处理文件；
- C4.5 失败处理：重试 2 次 → failures.jsonl；
- C4.6 输出 data/extracted/legacy_{herb,formula}.jsonl（envelope + provenance llm_extract 0.7）；
- C4.7 统计报告：成功率/字段覆盖率/耗时/token。

转换语义：
- HerbExtract → Herb + Effect×n + HAS_EFFECT×n；
- FormulaExtract → Formula + Herb(仅 NodeRef 引用) + COMPOSED_OF（raw_dose 保留原文，
  剂量折算属 C9 加载前的 D2 换算表职责）+ Pattern×n + TREATS_PATTERN×n
  + DiseaseTCM×n + TREATS_DISEASE×n；
- 转换期的本体校验失败（如枚举越界）→ quarantine 行，不计 LLM 失败。
"""

import asyncio  # 并发调度（Semaphore 限流 + gather 汇聚）
import hashlib  # 计算 content_hash（断点续跑的任务指纹）
import json  # JSON Schema 序列化 / envelope 落盘
import time  # 耗时统计（monotonic 时钟）
from collections.abc import Callable, Iterator  # 转换器/回调的类型注解
from dataclasses import dataclass, field  # ExtractJob/ExtractStats 数据类
from pathlib import Path  # 目录/文件路径处理

from pydantic import BaseModel, Field  # 抽取模型基类与字段描述（schema 注入源）

from tcm_kg.core.logging import get_logger  # 项目统一结构化日志
from tcm_kg.ontology import (  # 本体定义：实体/关系模型与领域枚举
    ComposedOf,  # 方剂-组成药关系（含剂量原文/角色）
    DiseaseTCM,  # 中医疾病实体
    Effect,  # 功效实体
    EntityType,  # 实体类型枚举（NodeRef 标签）
    Flavor,  # 药味枚举（辛甘酸苦咸...）
    Formula,  # 方剂实体
    FormulaRole,  # 君臣佐使角色枚举
    HasEffect,  # 中药-功效关系
    Herb,  # 中药实体
    Meridian,  # 归经枚举（肝心脾肺肾...）
    Nature,  # 药性枚举（寒热温凉平）
    NodeRef,  # 关系端点引用（不重复建实体）
    Pattern,  # 证候实体
    Provenance,  # 溯源记录（source/confidence/method）
    ProvenanceMethod,  # 溯源方法枚举
    Toxicity,  # 毒性枚举（无毒/小毒/有毒/大毒）
    TreatsDisease,  # 方剂-中医疾病主治关系
    TreatsPattern,  # 方剂-证候主治关系
)
from tcm_kg.pipeline.parsers.base import (  # envelope 协议（解析层统一输出格式）
    Envelope,  # envelope 类型别名
    entity_envelope,  # 实体信封构造器
    relation_envelope,  # 关系信封构造器
)

logger = get_logger(__name__)  # 模块级 logger

HERB_SOURCE_ID = "src-legacy-txt"  # 数据源标识：legacy 百科 txt（登记入溯源）
CONFIDENCE_LLM = 0.7  # LLM 抽取置信度（低于结构化源，供下游过滤）
RETRIES = 2  # 首次 + 2 次重试（C4.5）
MAX_TEXT_CHARS = 6000  # legacy txt 截断上限（百科文本头部信息最密）


# ---------- 抽取模型（LLM 输出目标，C4.1 schema 注入源） ----------


class ComponentExtract(BaseModel):
    """方剂组成中的单味药。"""

    herb: str = Field(description="中药规范名")
    dose_text: str | None = Field(default=None, description="剂量原文，如 三两 / 9g；缺失留空")
    role: FormulaRole | None = Field(default=None, description="君/臣/佐/使；未明示留空")


class HerbExtract(BaseModel):
    """中药百科文本结构化抽取目标。"""

    name: str = Field(description="中药规范名（词条标题）")
    aliases: list[str] = Field(default_factory=list, description="别名/异名")
    nature: Nature | None = Field(default=None, description="药性；未明示留空")
    flavors: list[Flavor] = Field(default_factory=list, description="药味列表")
    meridians: list[Meridian] = Field(default_factory=list, description="归经列表")
    toxicity: str | None = Field(
        default=None, description='毒性："无毒/小毒/有毒/大毒"；未明示留空'
    )
    dose_range: str | None = Field(default=None, description="常用剂量区间原文")
    efficacy_terms: list[str] = Field(
        default_factory=list, description="功效术语列表（短词，如 补气固表）"
    )


class FormulaExtract(BaseModel):
    """方剂百科文本结构化抽取目标。"""

    name: str = Field(description="方剂规范名（词条标题）")
    source_book: str | None = Field(default=None, description="出处典籍名")
    efficacy: str | None = Field(default=None, description="功用摘要")
    usage: str | None = Field(default=None, description="用法原文")
    components: list[ComponentExtract] = Field(default_factory=list, description="组成药物")
    treats_patterns: list[str] = Field(default_factory=list, description="主治证候名列表")
    treats_diseases: list[str] = Field(default_factory=list, description="主治中医疾病名列表")


_EXTRACT_CLASSES: dict[str, type[BaseModel]] = {  # kind → 抽取目标模型
    "herb": HerbExtract,  # 中药词条抽取模型
    "formula": FormulaExtract,  # 方剂词条抽取模型
}

# 系统提示词：抽取引擎角色设定 + 5 条领域规则（防臆造/规范词/枚举约束/剂量不换算）
SYSTEM_PROMPT = (
    "你是中医药知识图谱的数据抽取引擎。从用户提供的百科文本中抽取结构化知识，"
    "严格遵循输出 JSON Schema。\n\n规则：\n"
    "1. 只抽取文本明确陈述的内容；未提及的字段留空（null/空列表），禁止臆造。\n"
    "2. 术语用简体中文规范词；功效术语拆分为短词列表"
    '（如“补气固表、利尿托毒”→["补气固表","利尿托毒"]）。\n'
    "3. 毒性只允许：无毒/小毒/有毒/大毒；药性药味归经严格按枚举取值。\n"
    "4. 剂量保留原文写法（含古典单位），不做换算。\n"
    "5. 组成药物只录文本明确列出的药味及其剂量/君臣佐使角色。"
)


# 构造用户消息：词条标题 + 截断正文（头部信息最密，截断上限 6000 字）
def build_user_prompt(title: str, text: str) -> str:
    """用户消息：词条标题 + 截断正文。"""
    body = text[:MAX_TEXT_CHARS]  # 截断正文（头部信息最密）
    return f"【词条标题】{title}\n\n【正文】\n{body}"  # 标题 + 正文拼接为用户消息


# ---------- 抽取结果 → 本体 envelope ----------


def _prov() -> Provenance:
    return Provenance(  # 构造溯源：legacy 源 + LLM 置信度 + 抽取方法
        source_id=HERB_SOURCE_ID, confidence=CONFIDENCE_LLM, method=ProvenanceMethod.LLM_EXTRACT
    )


# HerbExtract → Herb + Effect×n + HAS_EFFECT×n（功效术语逐个建实体与边）
def herb_to_envelopes(extract: HerbExtract) -> Iterator[Envelope]:
    """HerbExtract → Herb + Effect×n + HAS_EFFECT×n。"""
    yield entity_envelope(  # 产出 Herb 实体信封
        Herb(
            name=extract.name,  # 中药规范名
            aliases=extract.aliases,  # 别名/异名列表
            nature=extract.nature,  # 药性（寒热温凉平）
            flavors=extract.flavors,  # 药味列表
            meridians=extract.meridians,  # 归经列表
            toxicity=_normalize_toxicity(extract.toxicity),  # 毒性（归一化为枚举）
            dose_range=extract.dose_range,  # 常用剂量区间原文
            standard_ref=None,  # 无药典标准引用（LLM 抽取不提供）
            provenance=_prov(),  # 溯源信息
        )
    )
    herb_ref = NodeRef(label=EntityType.HERB, name=extract.name)  # 中药节点引用（作关系起点）
    for term in extract.efficacy_terms:  # 遍历功效术语
        if not term.strip():  # 跳过空白项
            continue
        yield entity_envelope(Effect(name=term.strip(), provenance=_prov()))  # 功效实体
        yield relation_envelope(  # HAS_EFFECT 关系（中药→功效）
            HasEffect(
                start=herb_ref,  # 起点：中药
                end=NodeRef(label=EntityType.EFFECT, name=term.strip()),  # 终点：功效
                provenance=_prov(),  # 溯源信息
            )
        )


def _normalize_toxicity(raw: str | None) -> Toxicity | None:
    """LLM 输出的毒性文本 → Toxicity 枚举（宽松归一，非法值 None）。"""
    if not raw:  # 文本未提及毒性
        return None  # 返回空（不臆造）
    mapping = {  # 毒性文本 → Toxicity 枚举映射
        "无毒": Toxicity.NONE,  # 无毒
        "小毒": Toxicity.SLIGHT,  # 小毒
        "有毒": Toxicity.TOXIC,  # 有毒
        "大毒": Toxicity.HIGHLY_TOXIC,  # 大毒
    }
    return mapping.get(raw.strip())  # 未匹配返回 None（宽松归一，非法值不抛错）


def formula_to_envelopes(extract: FormulaExtract) -> Iterator[Envelope]:
    """FormulaExtract → Formula + 组成/主治边（引用节点不重复建实体）。"""
    yield entity_envelope(  # 产出 Formula 实体信封
        Formula(
            name=extract.name,  # 方剂规范名
            source_book=extract.source_book,  # 出处典籍名
            efficacy=extract.efficacy,  # 功用摘要
            usage=extract.usage,  # 用法原文
            provenance=_prov(),  # 溯源信息
        )
    )
    formula_ref = NodeRef(label=EntityType.FORMULA, name=extract.name)  # 方剂节点引用（作关系起点）
    for comp in extract.components:  # 遍历组成药物
        herb_name = comp.herb.strip()  # 规范化药名（去首尾空白）
        if not herb_name:  # 空名跳过
            continue
        # 组成药建占位 Herb 实体（仅最小字段，丰富属性留给对齐/其他源）
        yield entity_envelope(Herb(name=herb_name, provenance=_prov()))
        yield relation_envelope(  # COMPOSED_OF 关系（方剂→组成药）
            ComposedOf(
                start=formula_ref,  # 起点：方剂
                end=NodeRef(label=EntityType.HERB, name=herb_name),  # 终点：组成药
                raw_dose=comp.dose_text,  # 剂量原文（不换算，D2 换算表职责）
                role=comp.role,  # 君臣佐使角色
                provenance=_prov(),  # 溯源信息
            )
        )
    for pattern_name in extract.treats_patterns:  # 遍历主治证候
        if not pattern_name.strip():  # 空名跳过
            continue
        yield entity_envelope(Pattern(name=pattern_name.strip(), provenance=_prov()))  # 证候实体
        yield relation_envelope(  # TREATS_PATTERN 关系（方剂→证候）
            TreatsPattern(
                start=formula_ref,  # 起点：方剂
                end=NodeRef(label=EntityType.PATTERN, name=pattern_name.strip()),  # 终点：证候
                provenance=_prov(),  # 溯源信息
            )
        )
    for disease_name in extract.treats_diseases:  # 遍历主治中医疾病
        if not disease_name.strip():  # 空名跳过
            continue
        yield entity_envelope(DiseaseTCM(name=disease_name.strip(), provenance=_prov()))  # 疾病实体
        yield relation_envelope(  # TREATS_DISEASE 关系（方剂→中医疾病）
            TreatsDisease(
                start=formula_ref,  # 起点：方剂
                end=NodeRef(label=EntityType.DISEASE_TCM, name=disease_name.strip()),  # 终点：疾病
                provenance=_prov(),  # 溯源信息
            )
        )


# kind → 转换器映射（herb→herb_to_envelopes / formula→formula_to_envelopes）
_CONVERTERS: dict[str, Callable[[BaseModel], Iterator[Envelope]]] = {
    "herb": herb_to_envelopes,  # type: ignore[dict-item]
    "formula": formula_to_envelopes,  # type: ignore[dict-item]
}


# ---------- 编排（C4.3-C4.7） ----------


# 一个待抽取词条（kind + 标题 + 正文），支持计算 content_hash 用于断点
@dataclass
class ExtractJob:
    """一个待抽取词条。"""

    kind: str  # herb | formula
    title: str  # 词条标题（文件名去扩展名）
    text: str  # 词条正文全文

    def content_hash(self) -> str:
        payload = f"{self.kind}\x00{self.title}\x00{self.text}"  # 三元组拼接（\0 防碰撞）
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()  # sha256 任务指纹


@dataclass
class ExtractStats:
    """C4.7 统计报告。"""

    total: int = 0  # 任务总数
    succeeded: int = 0  # 成功任务数
    failed: int = 0  # 失败任务数（重试后仍失败）
    skipped: int = 0  # 断点跳过数
    quarantined: int = 0  # 隔离数（转换期本体校验失败）
    envelopes: dict[str, int] = field(default_factory=dict)  # envelope 计数：label/type → 数量
    field_coverage: dict[str, float] = field(default_factory=dict)  # 字段覆盖率：字段名 → 比例
    tokens: int | None = None  # token 总消耗（网关未上报则 None）
    elapsed_seconds: float = 0.0  # 总耗时（秒）

    # 成功率 = 成功 / (成功 + 失败)；无任务记 1.0
    @property
    def success_rate(self) -> float:
        attempted = self.succeeded + self.failed  # 实际尝试任务数
        return self.succeeded / attempted if attempted else 1.0  # 无任务记 100%


class Checkpoint:
    """C4.4 断点续跑：已完成的 content_hash 集合（JSON 持久化）。"""

    def __init__(self, path: Path) -> None:
        self.path = path  # 持久化文件路径（checkpoint.json）
        self.done: set[str] = set()  # 已完成任务的 content_hash 集合
        if path.exists():  # 已有 checkpoint 则恢复
            data = json.loads(path.read_text(encoding="utf-8"))  # 读取 JSON
            self.done = set(data.get("done", []))  # 恢复已完成集合

    def mark(self, content_hash: str) -> None:
        self.done.add(content_hash)  # 标记该任务已完成
        self.path.parent.mkdir(parents=True, exist_ok=True)  # 确保父目录存在
        self.path.write_text(  # 全量落盘（sorted 保证内容稳定、diff 友好）
            json.dumps({"done": sorted(self.done)}, ensure_ascii=False), encoding="utf-8"
        )


async def _extract_with_retry(
    llm: object,
    job: ExtractJob,
    *,
    retries: int = RETRIES,
) -> tuple[BaseModel, int | None]:
    """单任务：重试链（首次 + retries 次），全败抛最后一次异常。"""
    model_cls = _EXTRACT_CLASSES[job.kind]  # 按任务类型选择抽取目标模型
    system = (  # 系统提示词 + 目标模型 JSON Schema（驱动原生 structured output）
        SYSTEM_PROMPT
        + f"\n\n输出 JSON Schema：\n{json.dumps(model_cls.model_json_schema(), ensure_ascii=False)}"
    )
    user = build_user_prompt(job.title, job.text)  # 用户消息（标题 + 截断正文）
    last_exc: Exception | None = None  # 记录最后一次异常
    for attempt in range(retries + 1):  # 尝试链：首次 + retries 次重试
        try:
            result = await llm.extract(model_cls, system, user)  # type: ignore[attr-defined]
            return result.data, result.tokens  # 成功：返回结构化数据与 token 数
        except Exception as exc:  # noqa: BLE001 网关错误形态多样
            last_exc = exc  # 保存异常
            logger.warning("extract_retry", title=job.title, attempt=attempt, error=str(exc)[:200])
            if attempt < retries:  # 还有剩余重试机会
                await asyncio.sleep(2**attempt)  # 指数退避（1s、2s）
    raise last_exc if last_exc else RuntimeError("unreachable")  # 全败：抛最后一次异常


# 批量抽取主流程：并发（Semaphore）+ 断点续跑 + 失败隔离 + 字段覆盖率统计
async def run_extraction(
    jobs: list[ExtractJob],
    llm: object,
    out_dir: Path,
    *,
    concurrency: int = 5,
    checkpoint: Checkpoint | None = None,
    on_quarantine: Callable[[str, str, dict[str, str]], None] | None = None,
) -> ExtractStats:
    """批量抽取主流程（并发 + 断点 + 隔离 + 统计）。"""
    stats = ExtractStats(total=len(jobs))  # 初始化统计（含任务总数）
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    output_files = {  # 每类一个输出文件句柄（legacy_herb/legacy_formula）
        kind: (out_dir / f"legacy_{kind}.jsonl").open("w", encoding="utf-8")
        for kind in _EXTRACT_CLASSES
    }
    semaphore = asyncio.Semaphore(concurrency)  # 并发闸门：限制同时在飞的请求数
    tokens_total = 0  # token 累计器
    field_values: dict[str, list[bool]] = {}  # 字段出现标记收集（算覆盖率）
    started = time.monotonic()  # 计时起点

    async def one(job: ExtractJob) -> None:  # 单任务协程（gather 并发调度）
        nonlocal tokens_total  # 允许闭包内累计 token
        async with semaphore:  # 获取信号量（超出并发数则等待）
            content_hash = job.content_hash()  # 计算任务指纹
            if checkpoint is not None and content_hash in checkpoint.done:  # 断点命中
                stats.skipped += 1  # 跳过计数
                return  # 不重复抽取
            try:
                data, tokens = await _extract_with_retry(llm, job)  # 抽取（含重试链）
            except Exception as exc:  # noqa: BLE001
                stats.failed += 1  # 失败计数
                failures_path = out_dir / "failures.jsonl"  # 失败清单文件
                with failures_path.open("a", encoding="utf-8") as fh:  # 追加模式打开
                    fh.write(  # 写失败记录：title/kind/错误摘要（截 500 字）
                        json.dumps(
                            {"title": job.title, "kind": job.kind, "error": str(exc)[:500]},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                return  # 该任务结束（失败隔离，不中断整体）
            if tokens:  # 网关返回了 token 数
                tokens_total += tokens  # 累加
            try:
                envelopes = list(_CONVERTERS[job.kind](data))  # 抽取结果 → 本体 envelope
            except Exception as exc:  # noqa: BLE001 本体校验失败 → 隔离
                stats.quarantined += 1  # 隔离计数（不计 LLM 失败）
                if on_quarantine is not None:  # 配置了隔离回调
                    # 通知外层（source/错误/上下文）
                    on_quarantine(HERB_SOURCE_ID, str(exc), {"title": job.title, "kind": job.kind})
                return  # 不计成功
            for envelope in envelopes:  # 逐条写出 envelope
                # 落盘（ensure_ascii=False 保留中文原文）
                output_files[job.kind].write(json.dumps(envelope, ensure_ascii=False) + "\n")
                # 分类键：实体取 label，关系取 type
                key = envelope.get("label") or envelope.get("type") or "?"
                stats.envelopes[key] = stats.envelopes.get(key, 0) + 1  # 按类别计数
            stats.succeeded += 1  # 成功计数
            if checkpoint is not None:  # 启用断点续跑
                checkpoint.mark(content_hash)  # 标记完成并立即落盘
            for field_name, value in data.model_dump().items():  # 遍历模型全部字段
                present = bool(value) or value is False  # 非空即出现（False 显式值也算出现）
                field_values.setdefault(field_name, []).append(present)  # 收集出现标记

    await asyncio.gather(*(one(job) for job in jobs))  # 并发执行全部任务并等待
    for fh in output_files.values():  # 关闭全部输出文件
        fh.close()
    stats.tokens = tokens_total or None  # 0 归一为 None（未消耗）
    stats.elapsed_seconds = round(time.monotonic() - started, 2)  # 总耗时（保留 2 位小数）
    stats.field_coverage = {  # 字段覆盖率 = 出现次数 / 任务数（4 位小数）
        name: round(sum(flags) / len(flags), 4) for name, flags in field_values.items() if flags
    }
    return stats  # 返回统计结果


def format_extract_report(stats: ExtractStats) -> str:
    """C4.7 人类可读报告。"""
    envelopes = ", ".join(f"{k} {v}" for k, v in sorted(stats.envelopes.items()))  # envelope 统计串
    # 覆盖率串（各字段非空占比）
    coverage = ", ".join(f"{k} {v:.0%}" for k, v in sorted(stats.field_coverage.items()))
    tokens = stats.tokens if stats.tokens is not None else "n/a"  # 无 token 数据记 n/a
    return (  # 拼接多行报告文本
        f"抽取统计：总 {stats.total}｜成功 {stats.succeeded}（{stats.success_rate:.1%}）"
        f"｜失败 {stats.failed}｜跳过(断点) {stats.skipped}｜隔离 {stats.quarantined}\n"
        f"envelope：{envelopes}\n字段覆盖率：{coverage}\n"
        f"token：{tokens}｜耗时 {stats.elapsed_seconds}s"
    )


def discover_legacy_jobs(raw_dir: Path, kind: str) -> list[ExtractJob]:
    """legacy 目录 → ExtractJob 列表（zhongyao→herb / fangji→formula）。"""
    subdir = {"herb": "legacy/zhongyao", "formula": "legacy/fangji"}[kind]  # kind → 目录映射
    base = raw_dir / subdir  # legacy txt 所在目录
    if not base.is_dir():  # 目录不存在（未下载 legacy 数据）
        return []  # 返回空任务列表
    jobs: list[ExtractJob] = []  # 任务收集器
    for txt in sorted(base.glob("*.txt")):  # 排序遍历（确定性顺序）
        title = txt.stem  # 文件名去扩展名作词条标题
        text = txt.read_text(encoding="utf-8", errors="ignore")  # 读正文（忽略坏字节）
        if text.strip():  # 跳过空文件
            jobs.append(ExtractJob(kind=kind, title=title, text=text))  # 生成抽取任务
    return jobs  # 返回任务列表


# CLI 入口：发现任务 + 断点续跑 + 全流程（kinds/limit/concurrency 可配）
async def extract_cli(
    raw_dir: Path,
    out_dir: Path,
    llm: object,
    *,
    kinds: list[str] | None = None,
    limit: int | None = None,
    concurrency: int = 5,
) -> ExtractStats:
    """CLI 入口：发现任务 + 断点 + 全流程。"""
    target_kinds = kinds or ["herb", "formula"]  # 未指定则默认中药+方剂两类
    jobs: list[ExtractJob] = []  # 任务收集器
    for kind in target_kinds:  # 逐类发现任务
        jobs.extend(discover_legacy_jobs(raw_dir, kind))  # 并入任务列表
    if limit is not None:  # 指定了任务数上限
        jobs = jobs[:limit]  # 截断（调试/试跑用）
    checkpoint = Checkpoint(out_dir / "checkpoint.json")  # 断点文件（输出目录下）
    # 执行批量抽取（并发 + 断点 + 隔离 + 统计）
    return await run_extraction(jobs, llm, out_dir, concurrency=concurrency, checkpoint=checkpoint)


__all__ = [  # 模块公开导出清单
    "ComponentExtract",  # 方剂组成抽取模型
    "ExtractJob",  # 抽取任务数据类
    "ExtractStats",  # 抽取统计数据类
    "FormulaExtract",  # 方剂抽取模型
    "HERB_SOURCE_ID",  # legacy 源标识常量
    "HerbExtract",  # 中药抽取模型
    "Checkpoint",  # 断点续跑管理器
    "discover_legacy_jobs",  # legacy 目录任务发现
    "extract_cli",  # CLI 入口
    "format_extract_report",  # 统计报告格式化
    "formula_to_envelopes",  # 方剂结果转换器
    "herb_to_envelopes",  # 中药结果转换器
    "run_extraction",  # 批量抽取主流程
]
