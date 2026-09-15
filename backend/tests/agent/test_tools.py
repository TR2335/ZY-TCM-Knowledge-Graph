# ===================================================================
# 文件：backend/tests/agent/test_tools.py（agent 层单测 · 消毒器与六工具）
# 用途：验证 §7.4 剂量隐藏矩阵全维度（caution 方剂/有毒药材/排除标记/旁路防护），
#       以及六个 Agent 工具在 FakeRepo 注入下的正确行为（含只读白名单）
# ===================================================================

"""E1.7 消毒器 + E1 工具单测：剂量隐藏矩阵 / 警示文案 / 白名单（FakeRepo）。"""

from typing import Any  # 类型标注

import pytest  # 测试框架

from tcm_kg.agent.sanitizer import (  # 被测对象①：消毒器
    MASK,  # 掩码占位符常量
    requires_mask, sanitize_cypher_rows, sanitize_formula, sanitize_herb,  # 消毒函数
)
from tcm_kg.agent.tools import AgentTools  # 被测对象②：工具集

# ---------- 消毒器：§7.4 剂量隐藏矩阵 ----------


def test_requires_mask_matrix() -> None:
    """矩阵判定：caution 方剂 / 有毒大毒药材 → True；其余 False。"""
    assert requires_mask("Formula", {"safety_level": "caution"})  # caution 方剂 → 掩码
    assert not requires_mask("Formula", {"safety_level": "safe"})  # safe → 不掩
    assert not requires_mask("Formula", {})  # 无级别 → 不掩
    assert requires_mask("Herb", {"toxicity": "有毒"})  # 有毒 → 掩码
    assert requires_mask("Herb", {"toxicity": "大毒"})  # 大毒 → 掩码
    assert not requires_mask("Herb", {"toxicity": "小毒"})  # 小毒 → 不掩（药典口径）
    assert not requires_mask("Herb", {"toxicity": "无毒"})  # 无毒 → 不掩
    assert requires_mask("Herb", {"is_excluded_in_formula": True})  # 排除标记 → 掩码
    assert not requires_mask("Herb", {})  # 无字段 → 不掩
    assert not requires_mask("Effect", {})  # 非药类实体 → 不掩


def test_sanitize_herb_masks_dose() -> None:
    """有毒药材：dose_range 掩码 + 标准警示。"""
    record = {"name": "附子", "toxicity": "有毒", "dose_range": "3-15g"}  # 有毒药材记录
    sanitized = sanitize_herb(record)  # 消毒
    assert sanitized["dose_range"] == MASK  # 剂量被掩码
    assert "执业中医师" in sanitized["_safety_warning"]  # 警示含就医引导
    assert "不提供剂量" in sanitized["_safety_warning"]  # 警示说明原因
    # 原记录不被修改（纯函数）
    assert record["dose_range"] == "3-15g"  # 源数据完好


def test_sanitize_herb_safe_passthrough() -> None:
    """无毒药材：正常展示。"""
    record = {"name": "黄芪", "toxicity": "无毒", "dose_range": "9-30g"}  # 无毒药材
    assert sanitize_herb(record) == record  # 原样透传（无任何改动）


def test_sanitize_formula_caution() -> None:
    """caution 方剂：usage 掩码 + 毒性组成剂量掩码 + 命中毒药材列表。"""
    record = {  # caution 方剂记录
        "name": "四逆汤",
        "usage": "水煎服，附子先煎",  # 用法含煎煮细节（安全敏感）
        "safety_level": "caution",
    }
    components = [  # 组成药列表
        {"herb": "附子", "dose": 9.0, "unit": "g", "raw_dose": "三两", "toxicity": "有毒"},  # 毒药
        {"herb": "干姜", "dose": 6.0, "unit": "g", "raw_dose": "一两五钱", "toxicity": None},  # 无毒
    ]
    sanitized = sanitize_formula(record, components)  # 消毒
    assert sanitized["usage"] == MASK  # 用法掩码
    warning = sanitized["_safety_warning"]  # 警示文案
    assert "附子" in warning  # 命中毒药材列表
    assert "方剂" in warning  # 语境说明
    comps = {c["herb"]: c for c in sanitized["_components"]}  # 组成索引
    assert comps["附子"]["dose"] is None  # 毒药数值剂量置空
    assert comps["附子"]["raw_dose"] == MASK  # 毒药原文剂量掩码
    assert comps["干姜"]["dose"] == 6.0  # 无毒组成正常


def test_sanitize_formula_safe() -> None:
    """safe 方剂：组成与用法正常展示。"""
    record = {"name": "四君子汤", "usage": "水煎服", "safety_level": "safe"}  # safe 方
    components = [{"herb": "人参", "dose": 9.0, "toxicity": "无毒"}]  # 无毒组成
    sanitized = sanitize_formula(record, components)  # 消毒
    assert sanitized["usage"] == "水煎服"  # 用法保留
    assert "_safety_warning" not in sanitized  # 无警示字段


def test_sanitize_cypher_rows_bypass_guard() -> None:
    """text2cypher 旁路防护：毒性行剂量字段兜底掩码。"""
    rows = [  # 三行模拟 Cypher 查询结果
        {"name": "附子", "toxicity": "有毒", "dose_range": "3-15g"},  # 毒药行
        {"name": "黄芪", "toxicity": "无毒", "dose_range": "9-30g"},  # 无毒行
        {"f": "某方", "safety_level": "caution", "usage": "原用法"},  # caution 方剂行
    ]
    sanitized = sanitize_cypher_rows(rows)  # 逐行消毒
    assert sanitized[0]["dose_range"] == MASK  # 毒药剂量掩码
    assert sanitized[1]["dose_range"] == "9-30g"  # 无毒行不掩
    assert sanitized[2]["usage"] == MASK  # caution 方剂用法掩码


# ---------- 工具层 ----------


class FakeRepo:
    """内存假图谱（脚本化查询结果队列）。"""

    def __init__(self) -> None:
        self.queries: list[str] = []  # 记录收到的查询（断言用）
        self._queue: list[list[dict[str, Any]]] = []  # 预设结果队列（FIFO）

    def enqueue(self, rows: list[dict[str, Any]]) -> None:
        """预设一次查询的返回行。"""
        self._queue.append(rows)  # 入队

    def run(self, query: str, **_params: Any) -> list[dict[str, Any]]:
        """模拟执行：记录查询并弹出队首结果。"""
        self.queries.append(query)  # 记录
        if self._queue:  # 队列非空
            return self._queue.pop(0)  # 弹出队首
        return []  # 空队列 → 空结果


def test_tool_get_herb_sanitized() -> None:
    """药材详情工具：有毒药材自动消毒（工具层集成消毒器）。"""
    repo = FakeRepo()  # 假图谱
    repo.enqueue(  # 预设附子查询结果
        [
            {
                "name": "附子",
                "toxicity": "有毒",
                "dose_range": "3-15g",  # 危险字段
                "effects": ["回阳救逆"],
            }
        ]
    )
    tools = AgentTools(repo)  # 工具集
    result = tools.get_herb("附子")  # 查询
    assert result is not None  # 有结果
    assert result["dose_range"] == MASK  # 剂量已掩码（消毒生效）
    assert result["_safety_warning"]  # 带警示


def test_tool_get_formula_sanitized() -> None:
    """方剂详情工具：caution 方剂自动消毒。"""
    repo = FakeRepo()  # 假图谱
    repo.enqueue(  # 预设四逆汤
        [
            {
                "name": "四逆汤",
                "usage": "水煎服",
                "safety_level": "caution",  # 谨慎级
                "components": [
                    {"herb": "附子", "dose": 9.0, "raw_dose": "三两", "toxicity": "有毒"}  # 毒组成
                ],
                "patterns": ["少阴病证"],
                "diseases": [],
            }
        ]
    )
    tools = AgentTools(repo)  # 工具集
    result = tools.get_formula("四逆汤")  # 查询
    assert result is not None  # 有结果
    assert result["usage"] == MASK  # 用法掩码
    assert result["_components"][0]["dose"] is None  # 组成剂量置空
    assert "附子" in result["_safety_warning"]  # 警示点名毒药


def test_tool_match_patterns_scoring() -> None:
    """辨证工具：评分结果透传（主症必配+次症加权在 Cypher 内实现）。"""
    repo = FakeRepo()  # 假图谱
    repo.enqueue(  # 预设辨证结果
        [
            {
                "pattern": "心脾两虚",  # 证候名
                "score": 4,  # 匹配分
                "primary_hits": ["心悸"],  # 命中主症
                "secondary_hits": ["失眠", "乏力"],  # 命中次症
            }
        ]
    )
    tools = AgentTools(repo)  # 工具集
    results = tools.match_patterns(["心悸", "失眠", "乏力"])  # 三症状辨证
    assert results[0]["pattern"] == "心脾两虚"  # 证候正确
    assert results[0]["primary_hits"] == ["心悸"]  # 主症命中
    assert set(results[0]["secondary_hits"]) == {"失眠", "乏力"}  # 次症命中


def test_tool_check_contraindications() -> None:
    """配伍禁忌检查工具：三类警示聚合（十八反/妊娠/毒性）。"""
    repo = FakeRepo()  # 假图谱
    repo.enqueue([{"a": "甘草", "b": "甘遂", "contra_type": "十八反", "verse": "藻戟遂芫俱战草"}])  # 反药对
    repo.enqueue([{"a": "巴豆", "level": "禁用"}])  # 妊娠禁用
    repo.enqueue([{"a": "附子", "toxicity": "有毒"}])  # 毒性警示
    tools = AgentTools(repo)  # 工具集
    result = tools.check_contraindications(["甘草", "甘遂", "巴豆", "附子"])  # 四味药检查
    assert result["has_conflicts"] is True  # 有冲突
    assert result["conflicts"][0]["type"] == "十八反"  # 反药类型
    assert result["conflicts"][0]["herb_a"] == "甘草"  # 药名
    assert result["conflicts"][0]["verse"] == "藻戟遂芫俱战草"  # 歌诀依据
    assert result["pregnancy_warnings"][0]["level"] == "禁用"  # 妊娠警示
    assert result["toxicity_warnings"][0]["herb"] == "附子"  # 毒性警示


def test_tool_run_cypher_whitelist() -> None:
    """只读白名单：写操作拒绝。"""
    repo = FakeRepo()  # 假图谱
    tools = AgentTools(repo)  # 工具集
    with pytest.raises(ValueError, match="仅允许只读"):  # DELETE 被拒
        tools.run_cypher("MATCH (n) DETACH DELETE n")
    with pytest.raises(ValueError, match="仅允许只读"):  # MERGE 被拒
        tools.run_cypher("MERGE (n:Herb {name:'x'})")
    repo.enqueue([{"name": "黄芪"}])  # 预设只读结果
    assert tools.run_cypher("MATCH (h:Herb) RETURN h.name AS name LIMIT 5") == [{"name": "黄芪"}]  # 只读放行
