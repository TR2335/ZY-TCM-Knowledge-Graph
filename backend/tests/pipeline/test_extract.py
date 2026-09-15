# ===================================================================
# 文件：backend/tests/pipeline/test_extract.py（pipeline 层单测 · LLM 抽取 C4）
# 用途：FakeLLM 注入零网络验证抽取全链路——结构转换、并发统计、断点续跑、
#       重试耗尽、隔离机制、CLI 端到端、prompt 截断
# ===================================================================

"""C4 LLM 抽取单测：FakeLLM 注入，验证转换/并发/断点/重试/统计（零网络）。"""

import asyncio  # 标准库：跑异步函数
import json  # 标准库：读 JSONL 产物
from pathlib import Path  # 标准库：tmp_path
from typing import Any  # 类型标注

import pytest  # 测试框架
from pydantic import BaseModel  # Pydantic 基类

from tcm_kg.ontology import ENTITY_MODELS, RELATION_MODELS, FormulaRole, Nature  # 本体注册表
from tcm_kg.pipeline.extract import (  # 被测对象：抽取模块组件
    Checkpoint, ComponentExtract, ExtractJob, ExtractStats, FormulaExtract, HerbExtract,  # 数据结构
    build_user_prompt, discover_legacy_jobs, extract_cli, format_extract_report,  # 流程函数
    formula_to_envelopes, herb_to_envelopes, run_extraction,  # 核心转换
)


class FakeResult(BaseModel):
    """LLM 返回包装：抽取数据 + token 数。"""
    data: BaseModel  # 结构化抽取结果
    tokens: int | None = None  # token 消耗（可缺省）


class FakeLLM:
    """按标题返回预设结果；可注入异常序列模拟网关抖动。"""

    def __init__(
        self,
        responses: dict[str, Any],  # 标题 → 预设抽取结果
        fail_first: dict[str, int] | None = None,  # 标题 → 先失败几次（模拟 503）
    ):
        self.responses: dict[str, Any] = responses  # 存响应表
        self.fail_first = fail_first or {}  # 存失败计划
        self.calls: list[str] = []  # 调用记录（断言重试次数用）

    async def extract(self, model_cls: type[BaseModel], system: str, user: str) -> FakeResult:
        """模拟 LLM 结构化抽取：按 user prompt 里的标题查表返回。"""
        # 从 prompt 文本抠出标题（格式约定：...【词条标题】xxx\n...）
        title = user.split("】")[1].split("\n")[0]
        self.calls.append(title)  # 记录调用
        remaining = self.fail_first.get(title, 0)  # 该标题还剩几次"注定失败"
        if remaining > 0:  # 有失败配额
            self.fail_first[title] = remaining - 1  # 消耗一次
            raise ConnectionError("gateway 503")  # 模拟网关抖动
        if title not in self.responses:  # 无预设
            raise ValueError(f"no fixture for {title}")  # 视为抽取失败
        return FakeResult(data=self.responses[title], tokens=123)  # 固定 123 token


def _herb_fixture() -> HerbExtract:
    """标准药材抽取结果 fixture：黄芪（带功效词条）。"""
    return HerbExtract(
        name="黄芪",  # 药名
        aliases=["北芪"],  # 异名
        nature=Nature.SLIGHTLY_WARM,  # 微温
        flavors=[],  # 空（覆盖字段缺省）
        meridians=[],
        toxicity=None,  # 无毒
        dose_range="9-30g",  # 剂量
        efficacy_terms=["补气固表", "利尿托毒"],  # 两个功效词条
    )


def test_herb_to_envelopes() -> None:
    """Herb 转换：1 Herb + 2 Effect + 2 HAS_EFFECT，provenance=llm_extract/0.7。"""
    envelopes = list(herb_to_envelopes(_herb_fixture()))  # 执行转换
    labels = [e["label"] for e in envelopes if e["kind"] == "entity"]  # 实体标签列表
    assert labels.count("Herb") == 1  # 1 个药材
    assert labels.count("Effect") == 2  # 2 个功效
    edges = [e for e in envelopes if e["kind"] == "relation"]  # 关系边
    assert {e["type"] for e in edges} == {"HAS_EFFECT"}  # 全部是功效边
    herb = next(e for e in envelopes if e.get("label") == "Herb")["record"]  # 药材记录
    assert herb["provenance"]["method"] == "llm_extract"  # 溯源方法：LLM 抽取
    assert herb["provenance"]["confidence"] == 0.7  # LLM 抽取默认置信度
    assert herb["provenance"]["source_id"] == "src-legacy-txt"  # 指向 legacy txt 源
    assert herb["dose_range"] == "9-30g"  # 剂量透传
    # 全部 record 回验本体
    for e in envelopes:  # 逐条校验
        if e["kind"] == "entity":
            assert ENTITY_MODELS[e["label"]].model_validate(e["record"])  # 实体模型
        else:
            assert RELATION_MODELS["HasEffect"].model_validate(e["record"])  # 关系模型


def test_formula_to_envelopes() -> None:
    """Formula 转换：组成/主治证候/主治疾病 三类边 + raw_dose 保留原文。"""
    extract = FormulaExtract(  # 四逆汤抽取结果
        name="四逆汤",
        source_book="伤寒论",
        efficacy="回阳救逆",
        usage="水煎服",
        components=[  # 三味组成药
            ComponentExtract(herb="附子", dose_text="生附子一枚", role=FormulaRole.MONARCH),  # 君
            ComponentExtract(herb="干姜", dose_text="一两五钱", role=FormulaRole.MINISTER),  # 臣
            ComponentExtract(herb="炙甘草", dose_text=None, role=None),  # 无剂量无角色
        ],
        treats_patterns=["少阴病证"],  # 主治证候
        treats_diseases=["寒厥"],  # 主治疾病
    )
    envelopes = list(formula_to_envelopes(extract))  # 执行转换
    types = [e.get("type") for e in envelopes if e["kind"] == "relation"]  # 边类型列表
    assert types.count("COMPOSED_OF") == 3  # 3 条组成边
    assert types.count("TREATS_PATTERN") == 1  # 1 条主治证候
    assert types.count("TREATS_DISEASE") == 1  # 1 条主治疾病
    labels = [e.get("label") for e in envelopes if e["kind"] == "entity"]  # 实体标签
    assert labels.count("Formula") == 1  # 1 方剂
    assert labels.count("Herb") == 3  # 3 药材
    assert labels.count("Pattern") == 1  # 1 证候
    composed = [e["record"] for e in envelopes if e.get("type") == "COMPOSED_OF"]  # 组成边记录
    assert composed[0]["raw_dose"] == "生附子一枚"  # 原文剂量保留（保真）
    assert composed[0]["role"] == "君"  # 君药角色
    assert composed[0]["dose"] is None  # 折算属 C9 职责（此处不数值化）
    # 回验
    for e in envelopes:  # 逐条回验本体
        if e["kind"] == "entity":
            assert ENTITY_MODELS[e["label"]].model_validate(e["record"])
        else:
            cls_name = {  # 边类型 → 模型类名
                "COMPOSED_OF": "ComposedOf",
                "TREATS_PATTERN": "TreatsPattern",
                "TREATS_DISEASE": "TreatsDisease",
            }[e["type"]]
            assert RELATION_MODELS[cls_name].model_validate(e["record"])


def test_run_extraction_concurrency_and_stats(tmp_path: Path) -> None:
    """并发批量抽取：成功/失败/token/字段覆盖率统计。"""
    responses = {  # 两个预设
        "黄芪": _herb_fixture(),  # 正常
        "人参": HerbExtract(name="人参", efficacy_terms=["大补元气"]),  # 简化
    }
    llm = FakeLLM(responses, fail_first={"人参": 2})  # 人参前两次失败 → 重试成功
    jobs = [  # 三个任务
        ExtractJob(kind="herb", title="黄芪", text="黄芪正文"),  # 一次成功
        ExtractJob(kind="herb", title="人参", text="人参正文"),  # 两次失败后成功
        ExtractJob(kind="herb", title="断肠草", text="断肠草正文"),  # 无 fixture → 失败
    ]
    stats = asyncio.run(run_extraction(jobs, llm, tmp_path, concurrency=3))  # 并发 3 执行
    assert stats.total == 3  # 总数
    assert stats.succeeded == 2  # 成功 2
    assert stats.failed == 1  # 失败 1（断肠草）
    assert stats.success_rate == pytest.approx(2 / 3)  # 成功率
    assert stats.tokens == 123 * 2  # token 总数（两成功各 123）
    # 输出文件含 envelope
    lines = (tmp_path / "legacy_herb.jsonl").read_text(encoding="utf-8").splitlines()  # 成功产物
    assert any(json.loads(line).get("label") == "Herb" for line in lines)  # 有药材实体
    # 失败清单
    failures = (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()  # 失败清单
    assert len(failures) == 1  # 一条
    assert json.loads(failures[0])["title"] == "断肠草"  # 是断肠草
    # 人参经过 3 次调用（2 失败 + 1 成功）
    assert llm.calls.count("人参") == 3  # 重试生效


def test_checkpoint_resume(tmp_path: Path) -> None:
    """C4.4 断点续跑：第二次运行跳过已完成的词条。"""
    llm = FakeLLM({"黄芪": _herb_fixture()})  # 一个预设
    checkpoint = Checkpoint(tmp_path / "checkpoint.json")  # 断点文件
    jobs = [ExtractJob(kind="herb", title="黄芪", text="正文")]  # 一个任务
    first = asyncio.run(run_extraction(jobs, llm, tmp_path, checkpoint=checkpoint))  # 第一次
    second = asyncio.run(run_extraction(jobs, llm, tmp_path, checkpoint=checkpoint))  # 第二次（断点）
    assert first.succeeded == 1 and first.skipped == 0  # 首次成功 1
    assert second.succeeded == 0 and second.skipped == 1  # 二次跳过 1
    assert llm.calls.count("黄芪") == 1  # 未重复调用（省 token）


def test_retry_exhausted_fails(tmp_path: Path) -> None:
    """重试 2 次仍失败 → 计失败不中断其他任务。"""
    llm = FakeLLM({"黄芪": _herb_fixture()}, fail_first={"黄芪": 3})  # 黄芪注定全败
    jobs = [
        ExtractJob(kind="herb", title="黄芪", text="正文"),  # 将失败
        ExtractJob(kind="herb", title="人参", text="正文"),  # 将成功
    ]
    llm.responses["人参"] = HerbExtract(name="人参")  # 补人参预设
    stats = asyncio.run(run_extraction(jobs, llm, tmp_path))  # 执行
    assert stats.failed == 1  # 黄芪失败
    assert stats.succeeded == 1  # 人参成功（失败不传染）
    assert llm.calls.count("黄芪") == 3  # 1 + 2 重试


def test_quarantine_on_invalid_entity(tmp_path: Path) -> None:
    """转换期本体校验失败 → 隔离计数（不产生输出行）。"""
    import tcm_kg.pipeline.extract as extract_mod  # 引入模块本体（改转换表用）

    original = extract_mod._CONVERTERS["herb"]  # 备份原转换器

    def broken(_data: BaseModel) -> Any:
        raise ValueError("本体校验失败：name 长度不足")  # 模拟校验失败

    extract_mod._CONVERTERS["herb"] = broken  # 替换为坏转换器
    try:
        llm = FakeLLM({"黄芪": _herb_fixture()})  # LLM 正常返回
        jobs = [ExtractJob(kind="herb", title="黄芪", text="正文")]  # 一个任务
        stats = asyncio.run(run_extraction(jobs, llm, tmp_path))  # 执行
    finally:
        extract_mod._CONVERTERS["herb"] = original  # 无论成败恢复原转换器
    assert stats.quarantined == 1  # 隔离 1（坏数据不进产物）
    assert stats.succeeded == 0  # 成功 0


def test_discover_and_cli(tmp_path: Path) -> None:
    """legacy 目录发现 + extract_cli 端到端（FakeLLM）。"""
    raw = tmp_path / "raw"  # raw 目录
    (raw / "legacy" / "zhongyao").mkdir(parents=True)  # 中药目录
    (raw / "legacy" / "fangji").mkdir(parents=True)  # 方剂目录
    (raw / "legacy" / "zhongyao" / "黄芪.txt").write_text("黄芪正文", encoding="utf-8")  # 正常文件
    (raw / "legacy" / "zhongyao" / "空.txt").write_text("   ", encoding="utf-8")  # 空文件跳过
    (raw / "legacy" / "fangji" / "四逆汤.txt").write_text("四逆汤正文", encoding="utf-8")  # 方剂文件

    jobs = discover_legacy_jobs(raw, "herb")  # 发现药材任务
    assert [j.title for j in jobs] == ["黄芪"]  # 空文件被跳过
    assert discover_legacy_jobs(raw, "formula")[0].title == "四逆汤"  # 方剂任务发现

    llm = FakeLLM(  # 两类预设
        {
            "黄芪": _herb_fixture(),
            "四逆汤": FormulaExtract(name="四逆汤", components=[ComponentExtract(herb="附子")]),
        }
    )
    out = tmp_path / "extracted"  # 输出目录
    stats = asyncio.run(extract_cli(raw, out, llm, limit=2))  # CLI 入口端到端
    assert stats.succeeded == 2  # 两任务成功
    assert (out / "legacy_herb.jsonl").exists()  # 药材产物
    assert (out / "legacy_formula.jsonl").exists()  # 方剂产物
    assert (out / "checkpoint.json").exists()  # 断点文件
    report = format_extract_report(stats)  # 生成报告
    assert "成功 2" in report  # 报告含成功数
    assert "字段覆盖率" in report  # 报告含覆盖率


def test_prompt_and_models() -> None:
    """prompt 截断 + 抽取模型 schema 可导出（C4.1）。"""
    prompt = build_user_prompt("黄芪", "长文本" * 5000)  # 超长正文
    assert "【词条标题】黄芪" in prompt  # 标题头存在
    assert len(prompt) < 7000  # 正文被截断（控制 token）
    schema = HerbExtract.model_json_schema()  # 药材抽取 schema
    assert "efficacy_terms" in schema["properties"]  # 功效字段在
    assert FormulaExtract.model_json_schema()["properties"]["components"]  # 组成字段在


def test_extract_stats_defaults() -> None:
    """统计默认值：零任务时成功率 1.0、token None。"""
    stats = ExtractStats()  # 默认构造
    assert stats.success_rate == 1.0  # 空集成功率视作 1（不误导）
    assert stats.tokens is None  # 未消耗
