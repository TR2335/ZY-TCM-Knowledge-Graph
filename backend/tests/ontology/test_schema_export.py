# ===================================================================
# 文件：backend/tests/ontology/test_schema_export.py（ontology 层单测 · Schema 导出）
# 用途：验证 export_json_schemas() 产物——导出覆盖全部模型、字段/枚举完整、
#       剂量数值约束存在、边类型是合法 Cypher 标识符（可直接进 Neo4j）
# 覆盖验收点：A8.4（导出物与本体零偏差，可安全喂给 LLM 结构化抽取）
# ===================================================================

"""JSON Schema 导出与本体一致性自检（A8.4）。"""

from tcm_kg.ontology import (  # 被测对象：导出函数 + 三个注册表 + 版本号
    ACTIVE_RELATION_TYPES, ENTITY_MODELS, ONTOLOGY_VERSION, RELATION_MODELS, export_json_schemas,
)


def test_ontology_version_matches_design_doc() -> None:
    """ontology-design.md 当前为 v0.4（semver 0.4.0）；冻结后升 1.0。"""
    assert ONTOLOGY_VERSION == "0.4.0"  # 版本号与设计文档严格一致（升版必须同步文档）


def test_export_covers_all_models() -> None:
    """导出完备性：12 实体 + 19 关系一个不漏。"""
    schemas = export_json_schemas()  # 执行导出（模型名 → JSON Schema 字典）
    entity_labels = {label.value for label in ENTITY_MODELS}  # 实体标签集合
    relation_names = set(RELATION_MODELS)  # 关系模型名集合
    assert entity_labels | relation_names == set(schemas)  # 导出键 = 实体 ∪ 关系（不多不少）
    assert len(schemas) == 12 + 19  # 总数恰为 31


def test_herb_schema_properties() -> None:
    """Herb Schema 字段清单：核心字段齐全，必填仅 name+provenance。"""
    schema = export_json_schemas()["Herb"]  # 取药材模型导出
    props = schema["properties"]  # 字段定义字典
    expected = {  # 核心字段期望集（其他字段允许存在，此集必须齐）
        "name",          # 药名
        "aliases",       # 异名
        "pinyin",        # 拼音
        "latin",         # 拉丁学名
        "nature",        # 药性
        "flavors",       # 药味
        "meridians",     # 归经
        "toxicity",      # 毒性
        "dose_range",    # 剂量范围
        "pregnancy_level",  # 妊娠分级
        "icd11_code",    # ICD-11 编码
        "standard_ref",  # 标准参照
        "is_excluded_in_formula",  # 排除标记（安全字段）
        "provenance",    # 溯源（必填）
    }
    assert expected <= set(props)  # 期望集是实际字段集的子集
    assert set(schema["required"]) == {"name", "provenance"}  # 必填恰两项


def test_enum_values_in_schema() -> None:
    """枚举值必须原样出现在 JSON Schema（供 LLM 结构化抽取注入）。"""
    schema = export_json_schemas()["Herb"]  # 药材 Schema
    # 药性枚举八个值原样导出（LLM 抽取时只能从中选）
    assert schema["$defs"]["Nature"]["enum"] == [
        "寒", "微寒", "凉", "平", "温", "微温", "热", "大热",
    ]
    assert "有毒" in schema["$defs"]["Toxicity"]["enum"]  # 毒性枚举含"有毒"
    assert schema["properties"]["is_excluded_in_formula"]["default"] is False  # 排除标记默认关


def test_formula_safety_level_schema() -> None:
    """Formula 安全级别枚举：仅 safe/caution（高危不入库，ADR-013）。"""
    schema = export_json_schemas()["Formula"]  # 方剂 Schema
    assert schema["$defs"]["SafetyLevel"]["enum"] == ["safe", "caution"]  # 两级制


def test_composed_of_schema_edge_props() -> None:
    """ComposedOf 边属性：剂量/单位/原文/角色齐全，dose 有正数约束。"""
    schema = export_json_schemas()["ComposedOf"]  # 组成边 Schema
    props = schema["properties"]  # 属性定义
    assert {"start", "end", "provenance", "dose", "unit", "raw_dose", "role"} <= set(props)  # 属性齐全
    # dose 为 float | None：数值约束位于 anyOf 的 number 分支
    assert props["dose"]["anyOf"][0]["exclusiveMinimum"] == 0  # 必须 > 0（排他下界）


def test_relation_types_are_valid_cypher_identifiers() -> None:
    """边类型即 Cypher 关系类型，必须是合法标识符（全大写下划线）。"""
    for rel_type in ACTIVE_RELATION_TYPES:  # 遍历全部有效边类型
        assert rel_type.replace("_", "").isalpha()  # 去下划线后全是字母（无数字/特殊字符）
        assert rel_type == rel_type.upper()  # 全大写（Cypher 关系类型惯例）
