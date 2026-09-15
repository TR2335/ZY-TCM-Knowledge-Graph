# ===================================================================
# 文件：backend/tests/ontology/test_enums.py（ontology 层单测 · 枚举全集）
# 用途：逐个枚举断言成员值全集——与设计文档 ontology-design.md §4 零偏差（改枚举必须改文档）
# 核心保障：枚举是全图谱的"词汇表"，任何增删成员都会在此测试立即暴露
# ===================================================================

"""枚举全集单测（A8.4：全部枚举有测试，值与 ontology-design.md §4 零偏差）。"""

from enum import StrEnum  # 字符串枚举基类（Python 3.11+，成员值即字符串）

from tcm_kg.ontology import (  # 被测对象：全部 15 个枚举
    ContraType, DosageForm, DoseUnit, Flavor, FormulaRole, FormulaType, Meridian, Nature,
    PatternCategory, PregnancyLevel, ProvenanceMethod, SafetyLevel, SourceType, SynonymType, Toxicity,
)


def _assert_members(enum_type: type[StrEnum], expected: list[str]) -> None:
    """辅助断言：枚举成员值按定义顺序与期望列表完全一致（多一个少一个都失败）。"""
    assert [member.value for member in enum_type] == expected


def test_nature() -> None:
    """药性八级：寒热温凉谱系（从寒到大热）。"""
    _assert_members(Nature, ["寒", "微寒", "凉", "平", "温", "微温", "热", "大热"])


def test_flavor() -> None:
    """药味七种：酸苦甘辛咸涩淡。"""
    _assert_members(Flavor, ["酸", "苦", "甘", "辛", "咸", "涩", "淡"])


def test_meridian() -> None:
    """十二经脉：肺大肠胃脾心小肠膀胱肾心包三焦胆肝（手足三阴三阳）。"""
    _assert_members(
        Meridian,
        ["肺", "大肠", "胃", "脾", "心", "小肠", "膀胱", "肾", "心包", "三焦", "胆", "肝"],
    )


def test_toxicity() -> None:
    """毒性四级：无毒→小毒→有毒→大毒（药典口径）。"""
    _assert_members(Toxicity, ["无毒", "小毒", "有毒", "大毒"])


def test_safety_level() -> None:
    """方剂安全级别：仅 safe/caution 两级。"""
    # high_risk 不存在：被 L1 数据层过滤，不入库（ADR-013）
    _assert_members(SafetyLevel, ["safe", "caution"])  # 高危方剂根本不给入库机会


def test_pregnancy_level() -> None:
    """妊娠用药分级：禁用/忌用/慎用/无标记（由严到宽）。"""
    _assert_members(PregnancyLevel, ["禁用", "忌用", "慎用", "无标记"])


def test_formula_role() -> None:
    """方剂配伍角色：君臣佐使（七情配伍经典结构）。"""
    _assert_members(FormulaRole, ["君", "臣", "佐", "使"])


def test_formula_type() -> None:
    """方剂类型：经方/时方/中成药（ADR-011：中成药是类型非独立实体）。"""
    _assert_members(FormulaType, ["经方", "时方", "中成药"])


def test_dosage_form() -> None:
    """剂型八种：丸散膏丹颗粒口服液片胶囊。"""
    _assert_members(DosageForm, ["丸", "散", "膏", "丹", "颗粒", "口服液", "片", "胶囊"])


def test_dose_unit() -> None:
    """剂量单位：克/毫克/毫升三选一。"""
    _assert_members(DoseUnit, ["g", "mg", "ml"])


def test_synonym_type() -> None:
    """七情配伍（协同类）：相须/相使/相畏/相杀。"""
    _assert_members(SynonymType, ["相须", "相使", "相畏", "相杀"])


def test_contra_type() -> None:
    """配伍禁忌类型：十八反/十九畏（经典禁忌体系）。"""
    _assert_members(ContraType, ["十八反", "十九畏"])


def test_pattern_category() -> None:
    """证候类目八纲：八纲证到卫气营血证（GB/T 分类的顶层归纳）。"""
    _assert_members(
        PatternCategory,
        [
            "八纲证",        # 阴阳表里寒热虚实
            "病因证",        # 六淫七情等
            "气血津液证",    # 气虚血瘀痰饮等
            "脏腑证",        # 心脾两虚等
            "经络证",        # 经络病变
            "六经证",        # 太阳阳明少阳太阴少阴厥阴
            "三焦证",        # 上中下三焦
            "卫气营血证",    # 温病辨证
        ],
    )


def test_source_type() -> None:
    """数据来源五类：典籍/国家标准/药典/数据库/人工整理。"""
    _assert_members(SourceType, ["典籍", "国家标准", "药典", "数据库", "人工整理"])


def test_provenance_method() -> None:
    """知识获取方式四类：人工精选/数据库导入/LLM 抽取/人工复核。"""
    _assert_members(ProvenanceMethod, ["curated", "db_import", "llm_extract", "manual_review"])


def test_toxicity_rank_ordering() -> None:
    """毒性 rank 单调递增：无毒 < 小毒 < 有毒 < 大毒（比较用序数）。"""
    assert (
        Toxicity.NONE.rank < Toxicity.SLIGHT.rank < Toxicity.TOXIC.rank < Toxicity.HIGHLY_TOXIC.rank
    )


def test_toxicity_dose_masking_matrix() -> None:
    """§7.4 剂量隐藏矩阵：毒性 ∈ {有毒, 大毒} → 掩码；小毒及以下 → 正常展示。"""
    assert Toxicity.TOXIC.requires_dose_masking is True  # 有毒 → 必须隐藏剂量
    assert Toxicity.HIGHLY_TOXIC.requires_dose_masking is True  # 大毒 → 必须隐藏剂量
    assert Toxicity.NONE.requires_dose_masking is False  # 无毒 → 正常展示
    assert Toxicity.SLIGHT.requires_dose_masking is False  # 小毒 → 正常展示（药典口径）
