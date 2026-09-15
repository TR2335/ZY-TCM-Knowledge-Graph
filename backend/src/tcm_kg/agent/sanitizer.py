# ===================================================================
# 文件：sanitizer.py
# 用途：安全输出消毒器（L3 防线第一点），实现剂量隐藏矩阵和标准警示文案
# 所属层级：agent 层（L3 安全防线 · 工具层消毒出口，图谱→用户的第一道出口）
# 核心功能：
#   1. 按 ontology §7.4 剂量信息呈现矩阵判定是否触发掩码（requires_mask）
#   2. 方剂 safety_level=caution → usage/组成剂量掩码 + 警示（sanitize_formula）
#   3. 药材毒性∈{有毒,大毒}/is_excluded_in_formula → dose_range 掩码 + 警示（sanitize_herb）
#   4. text2cypher 原始行输出的兜底消毒（sanitize_cypher_rows，E1.7 旁路防护）
# 如何使用：
#   药材/方剂查询结果经过本模块消毒后才返回给用户；
#   API 层（F3）会再做第二道掩码，形成双点消毒。
# ===================================================================

"""E1.7 安全输出消毒器（ADR-013 L3）：剂量隐藏 + 强制警示（ontology §7.4）。

矩阵（§7.4 剂量信息呈现规则，用户决策）：
| 对象 | 判定条件 | 处理 |
|---|---|---|
| 方剂 | safety_level = caution | COMPOSED_OF.dose/raw_dose、Formula.usage 掩码 |
| 药材 | toxicity ∈ {有毒, 大毒} | Herb.dose_range 掩码 |
| 药材 | is_excluded_in_formula | 同上，且不给使用建议 |
| 其余 | — | 正常展示 |

标准警示文案（§7.4）：
⚠ 该{方剂/药材}含毒性成分（{命中毒药材列表}）。出于安全考虑，此处不提供
剂量与用法信息。请前往正规医疗机构，由执业中医师辨证后开具处方使用。

本模块是图谱→用户的第一出口（工具层）；API 层（F3）做第二道掩码。
"""

from typing import Any

from tcm_kg.ontology import Toxicity

# 标准就医警示文案模板：命中毒性时填充 kind（药材/方剂）与 toxic_list（命中毒药材列表）
WARNING_TEMPLATE = (
    "⚠ 该{kind}含毒性成分（{toxic_list}）。出于安全考虑，此处不提供剂量与用法信息。"
    "请前往正规医疗机构，由执业中医师辨证后开具处方使用。"
)

# 剂量信息掩码占位串：替换原剂量字段值，提示须由执业中医师开具
MASK = "【剂量信息已隐藏：含毒性成分，须由执业中医师开具】"


# 判定实体是否触发剂量掩码（§7.4 矩阵）：方剂看 safety_level，药材看毒性/方剂排除标记
def requires_mask(
    label: str, record: dict[str, Any], toxicity_lookup: dict[str, str] | None = None
) -> bool:
    """判定实体是否触发剂量掩码（§7.4 矩阵）。"""
    if label == "Formula":
        return record.get("safety_level") == "caution"
    if label == "Herb":
        toxicity = record.get("toxicity")
        if toxicity in (Toxicity.TOXIC.value, Toxicity.HIGHLY_TOXIC.value):
            return True
        if toxicity_lookup and toxicity_lookup.get(record.get("name", "")) in (
            Toxicity.TOXIC.value,
            Toxicity.HIGHLY_TOXIC.value,
        ):
            return True
        return bool(record.get("is_excluded_in_formula"))
    return False


# 药材记录消毒：命中矩阵则掩码 dose_range 并注入标准警示文案
def sanitize_herb(record: dict[str, Any]) -> dict[str, Any]:
    """药材记录消毒：命中矩阵 → 剂量掩码 + 警示。"""
    result = dict(record)
    if requires_mask("Herb", record):
        toxic_note = result.get("toxicity") or "毒性成分"
        if result.get("dose_range"):
            result["dose_range"] = MASK
        result["_safety_warning"] = WARNING_TEMPLATE.format(kind="药材", toxic_list=toxic_note)
    return result


def sanitize_formula(
    record: dict[str, Any], components: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """方剂记录消毒：caution → usage 掩码 + 组成剂量掩码 + 警示（命中毒药材列表）。

    components: [{herb, dose?, unit?, raw_dose?, toxicity?}, ...]
    """
    result = dict(record)
    components = components or []
    toxic_hits = [
        c["herb"]
        for c in components
        if c.get("toxicity") in (Toxicity.TOXIC.value, Toxicity.HIGHLY_TOXIC.value)
    ]
    if result.get("safety_level") == "caution" or toxic_hits:
        toxic_list = "、".join(sorted(set(toxic_hits))) or "毒性成分"
        if result.get("usage"):
            result["usage"] = MASK
        result["_safety_warning"] = WARNING_TEMPLATE.format(kind="方剂", toxic_list=toxic_list)
        sanitized_components = []
        for comp in components:
            comp = dict(comp)
            if comp.get("toxicity") in (Toxicity.TOXIC.value, Toxicity.HIGHLY_TOXIC.value):
                if comp.get("dose") is not None:
                    comp["dose"] = None
                    comp["raw_dose"] = MASK
                elif comp.get("raw_dose"):
                    comp["raw_dose"] = MASK
            sanitized_components.append(comp)
        result["_components"] = sanitized_components
    return result


# E1.7 旁路防护：对 text2cypher 原始行输出做保守兜底消毒（行含毒性标记则掩码剂量类字段）
def sanitize_cypher_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """E1.7 旁路防护：text2cypher 原始行输出的兜底消毒。

    规则（保守）：行内出现以下特征字段即掩码该字段值——
    - dose / raw_dose（方剂组成剂量）且行含 caution 或毒性标记；
    - dose_range 且行含 有毒/大毒。
    """
    sanitized: list[dict[str, Any]] = []
    for row in rows:
        row_text = " ".join(str(v) for v in row.values())
        row = dict(row)
        has_toxic_marker = "有毒" in row_text or "大毒" in row_text or "caution" in row_text
        if has_toxic_marker:
            for key in list(row.keys()):
                if key in ("dose", "raw_dose", "dose_range", "usage") and row[key]:
                    row[key] = MASK
        sanitized.append(row)
    return sanitized
