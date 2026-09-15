# ===================================================================
# 文件：backend/tests/ontology/test_relations.py（ontology 层单测 · 关系模型）
# 用途：逐条验证 20 类关系——合法样例可构造 + 定义域/值域违规被拒 + 边属性约束
# 核心保障：本体的"语法"（哪类实体能连哪类实体）在模型层硬校验，违规数据进不了图
# ===================================================================

"""关系模型合法/非法样例单测（A8.4）：定义域/值域校验与边属性。"""

from typing import Any, cast  # cast：故意绕过静态类型检查以触发运行时校验

import pytest  # 测试框架
from pydantic import ValidationError  # 校验失败异常

from tcm_kg.ontology import (  # 被测对象：全部关系模型与注册表
    ACTIVE_RELATION_TYPES, DEPRECATED_RELATION_TYPES, RELATION_MODELS,  # 注册表与弃用清单
    AliasedIn, AllaysSymptom, BelongsTo, ComposedOf, ContainedIn, ContraindicatedWith,  # 关系模型
    CorrespondsToDisease, CorrespondsToSymptom, EntityType, FromSource, HasEffect, HasSymptom,
    IndicatesMethod, Manifests, NodeRef, PregnancyContra, ProcessedFrom, Provenance,
    Restrains, SynergisticWith, TreatsDisease, TreatsPattern,
)
from tcm_kg.ontology.enums import (  # 关系属性枚举
    ContraType, DoseUnit, FormulaRole, PregnancyLevel, SynonymType,
)


def _ref(label: EntityType, name: str) -> NodeRef:
    """辅助：构造节点引用（label + name 定位图中节点）。"""
    return NodeRef(label=label, name=name)


def HERB(name: str) -> NodeRef:
    """辅助：药材节点引用。"""
    return _ref(EntityType.HERB, name)


def FORMULA(name: str) -> NodeRef:
    """辅助：方剂节点引用。"""
    return _ref(EntityType.FORMULA, name)


def test_composed_of_valid(prov: Provenance) -> None:
    """方剂组成边：方→药，可带剂量/原始剂量/君臣佐使角色。"""
    edge = ComposedOf(
        start=FORMULA("四逆汤"),  # 起点：方剂（定义域：Formula）
        end=HERB("附子"),  # 终点：药材（值域：Herb）
        dose=9.0,  # 标准化剂量（数值）
        unit=DoseUnit.GRAM,  # 剂量单位：克
        raw_dose="三两",  # 原始剂量文本（经方原文，保真）
        role=FormulaRole.MONARCH,  # 配伍角色：君药
        provenance=prov,
    )
    assert edge.start.name == "四逆汤"  # 起点正确
    assert edge.role == FormulaRole.MONARCH  # 角色正确


def test_composed_of_domain_violation(prov: Provenance) -> None:
    """定义域违规：药→药 不能连 COMPOSED_OF（必须是 方→药）。"""
    with pytest.raises(ValidationError, match="定义域违规"):  # 报错文案含"定义域违规"
        ComposedOf(start=HERB("黄芪"), end=HERB("附子"), provenance=prov)  # 起点类型错


def test_composed_of_negative_dose(prov: Provenance) -> None:
    """负剂量被拒：dose 必须 ≥ 0。"""
    with pytest.raises(ValidationError):
        ComposedOf(start=FORMULA("四逆汤"), end=HERB("附子"), dose=-3.0, provenance=prov)  # 负值违规


def test_has_effect_valid(prov: Provenance) -> None:
    """功效边：药→功效，可附原文依据。"""
    edge = HasEffect(
        start=HERB("黄芪"),  # 药材
        end=_ref(EntityType.EFFECT, "补气固表"),  # 功效
        source_text="补气固表，利尿托毒",  # 原文摘录（证据可回溯）
        provenance=prov,
    )
    assert edge.source_text is not None  # 原文保存成功


def test_treats_pattern_valid(prov: Provenance) -> None:
    """主治证候边：方→证。"""
    edge = TreatsPattern(
        start=FORMULA("归脾汤"),  # 方剂
        end=_ref(EntityType.PATTERN, "心脾两虚"),  # 证候
        provenance=prov,
    )
    assert edge.end.name == "心脾两虚"  # 终点正确


def test_treats_pattern_herb_start_allowed(prov: Provenance) -> None:
    """药→证 也合法：单味药同样可主治证候（定义域含 Herb+Formula）。"""
    assert TreatsPattern(
        start=HERB("黄芪"), end=_ref(EntityType.PATTERN, "气虚证"), provenance=prov
    )


def test_treats_disease_valid(prov: Provenance) -> None:
    """主治疾病边：方→中医疾病。"""
    assert TreatsDisease(
        start=FORMULA("银翘散"), end=_ref(EntityType.DISEASE_TCM, "风温"), provenance=prov
    )


def test_indicates_method_valid(prov: Provenance) -> None:
    """体现治法边：方→治法（麻黄汤体现汗法）。"""
    assert IndicatesMethod(
        start=FORMULA("麻黄汤"), end=_ref(EntityType.TREATMENT_METHOD, "汗法"), provenance=prov
    )


def test_indicates_method_domain_violation(prov: Provenance) -> None:
    """定义域违规：药不能连 INDICATES_METHOD（定义域仅 Formula，§3.2）。"""
    with pytest.raises(ValidationError, match="定义域违规"):
        IndicatesMethod(
            start=HERB("麻黄"), end=_ref(EntityType.TREATMENT_METHOD, "汗法"), provenance=prov
        )


def test_has_symptom_is_primary(prov: Provenance) -> None:
    """证候症状边：is_primary 主症标记，默认 False。"""
    edge = HasSymptom(
        start=_ref(EntityType.PATTERN, "心脾两虚"),  # 证候
        end=_ref(EntityType.SYMPTOM_TCM, "心悸"),  # 中医症状
        is_primary=True,  # 显式标主症
        provenance=prov,
    )
    assert edge.is_primary is True  # 主症标记生效
    default_edge = HasSymptom(  # 不传 is_primary 的对照
        start=_ref(EntityType.PATTERN, "心脾两虚"),
        end=_ref(EntityType.SYMPTOM_TCM, "健忘"),
        provenance=prov,
    )
    assert default_edge.is_primary is False  # 默认次症


def test_has_symptom_range_violation(prov: Provenance) -> None:
    """值域违规：HAS_SYMPTOM 终点只收中医症状（SymptomWM 被拒）。"""
    with pytest.raises(ValidationError, match="值域违规"):
        HasSymptom(
            start=_ref(EntityType.PATTERN, "心脾两虚"),
            end=_ref(EntityType.SYMPTOM_WM, "心悸"),  # 西医症状 → 违规
            provenance=prov,
        )


def test_allays_symptom_valid(prov: Provenance) -> None:
    """缓解症状边：药→中医症状（酸枣仁缓解失眠）。"""
    assert AllaysSymptom(
        start=HERB("酸枣仁"), end=_ref(EntityType.SYMPTOM_TCM, "失眠"), provenance=prov
    )


def test_corresponds_to_disease_valid(prov: Provenance) -> None:
    """中西医疾病对应边：可注明映射来源。"""
    edge = CorrespondsToDisease(
        start=_ref(EntityType.DISEASE_TCM, "眩晕"),  # 中医病名
        end=_ref(EntityType.DISEASE_WM, "原发性高血压"),  # 西医病名
        mapping_source="SymMap",  # 映射来源（哪个数据集给出的对应）
        provenance=prov,
    )
    assert edge.mapping_source == "SymMap"  # 来源保存


def test_corresponds_to_disease_wrong_pair(prov: Provenance) -> None:
    """端点类型错配：症状↔疾病 不允许（必须病↔病）。"""
    with pytest.raises(ValidationError):
        CorrespondsToDisease(
            start=_ref(EntityType.SYMPTOM_TCM, "心悸"),  # 起点应为 DiseaseTCM
            end=_ref(EntityType.DISEASE_WM, "原发性高血压"),
            provenance=prov,
        )


def test_corresponds_to_symptom_valid(prov: Provenance) -> None:
    """中西医症状对应边：症↔症（SymMap 的核心映射）。"""
    assert CorrespondsToSymptom(
        start=_ref(EntityType.SYMPTOM_TCM, "头痛"),
        end=_ref(EntityType.SYMPTOM_WM, "头痛"),
        mapping_source="SymMap",
        provenance=prov,
    )


def test_contraindicated_with_valid(prov: Provenance) -> None:
    """十八反：甘草 反 甘遂（藻戟遂芫俱战草）。"""
    edge = ContraindicatedWith(
        start=HERB("甘草"),  # 药材甲
        end=HERB("甘遂"),  # 药材乙
        type=ContraType.EIGHTEEN_ANTAGONISMS,  # 禁忌类型：十八反
        verse="藻戟遂芫俱战草",  # 歌诀原文（可追溯到经典）
        provenance=prov,
    )
    assert edge.type == ContraType.EIGHTEEN_ANTAGONISMS  # 类型正确


def test_contraindicated_with_range_violation(prov: Provenance) -> None:
    """值域违规：禁忌边两端必须都是药材（方剂被拒）。"""
    with pytest.raises(ValidationError, match="值域违规"):
        ContraindicatedWith(
            start=HERB("甘草"),
            end=FORMULA("四逆汤"),  # 终点应为 Herb
            type=ContraType.EIGHTEEN_ANTAGONISMS,
            provenance=prov,
        )


def test_pregnancy_contra_valid(prov: Provenance) -> None:
    """妊娠禁忌边：药→妊娠禁忌类目，带分级。"""
    edge = PregnancyContra(
        start=HERB("巴豆"),  # 药材
        end=_ref(EntityType.CATEGORY, "妊娠禁忌"),  # 固定指向妊娠禁忌类目
        level=PregnancyLevel.PROHIBITED,  # 分级：禁用
        provenance=prov,
    )
    assert edge.RELATION_TYPE == "PREGNANCY_CONTRA"  # 边类型常量正确


def test_pregnancy_contra_wrong_category(prov: Provenance) -> None:
    """终点必须是"妊娠禁忌"类目：指向其他类目被拒（防误用）。"""
    with pytest.raises(ValidationError, match="妊娠禁忌"):
        PregnancyContra(
            start=HERB("巴豆"),
            end=_ref(EntityType.CATEGORY, "补虚药"),  # 错误类目
            level=PregnancyLevel.PROHIBITED,
            provenance=prov,
        )


def test_synergistic_with_valid(prov: Provenance) -> None:
    """增效配伍边：石膏-知母 相须（协同增效）。"""
    assert SynergisticWith(
        start=HERB("石膏"), end=HERB("知母"), type=SynonymType.MUTUAL_REINFORCEMENT, provenance=prov
    )


def test_synergistic_with_rejects_restraint_type(prov: Provenance) -> None:
    """增效关系只允许 相须/相使（cast 绕过静态检查以触发运行时校验）。"""
    with pytest.raises(ValidationError):
        SynergisticWith(
            start=HERB("石膏"),
            end=HERB("知母"),
            type=cast("Any", SynonymType.MUTUAL_RESTRAINT),  # 相畏是"制约"语义，不属于增效
            provenance=prov,
        )


def test_restrains_valid(prov: Provenance) -> None:
    """制约配伍边：生姜制半夏毒（相杀）。"""
    assert Restrains(
        start=HERB("生姜"), end=HERB("半夏"), type=SynonymType.MUTUAL_DETOXICATION, provenance=prov
    )


def test_processed_from_valid(prov: Provenance) -> None:
    """炮制来源边：熟地黄 由 地黄 九蒸九晒制得。"""
    edge = ProcessedFrom(start=HERB("熟地黄"), end=HERB("地黄"), method="九蒸九晒", provenance=prov)
    assert edge.method == "九蒸九晒"  # 炮制方法保存


def test_belongs_to_valid(prov: Provenance) -> None:
    """归类边：药→类目（黄芪属补虚药类）。"""
    assert BelongsTo(start=HERB("黄芪"), end=_ref(EntityType.CATEGORY, "补虚药"), provenance=prov)


def test_belongs_to_domain_violation(prov: Provenance) -> None:
    """定义域违规：Source 不能连 BELONGS_TO（出处不归类目）。"""
    with pytest.raises(ValidationError, match="定义域违规"):
        BelongsTo(
            start=_ref(EntityType.SOURCE, "药典2020"),  # 起点类型不在定义域
            end=_ref(EntityType.CATEGORY, "补虚药"),
            provenance=prov,
        )


def test_from_source_valid_and_category_excluded(prov: Provenance) -> None:
    """知识出处边：实体→Source 合法；Category→Source 违规（类目是导航不是知识）。"""
    assert FromSource(start=HERB("黄芪"), end=_ref(EntityType.SOURCE, "药典2020"), provenance=prov)  # 合法
    with pytest.raises(ValidationError, match="定义域违规"):
        FromSource(
            start=_ref(EntityType.CATEGORY, "补虚药"),  # 类目不在 FROM_SOURCE 定义域
            end=_ref(EntityType.SOURCE, "药典2020"),
            provenance=prov,
        )


def test_from_source_source_never_starts(prov: Provenance) -> None:
    """§3.2：Source 只作为值域——Source → Source 的 FROM_SOURCE 边违规。"""
    with pytest.raises(ValidationError, match="定义域违规"):
        FromSource(
            start=_ref(EntityType.SOURCE, "药典2020"),  # Source 作起点被拒
            end=_ref(EntityType.SOURCE, "中医药学名词"),
            provenance=prov,
        )


def test_contained_in_valid(prov: Provenance) -> None:
    """成分包含边：成分→药材（黄芪甲苷含于黄芪），可注含量参照。"""
    assert ContainedIn(
        start=_ref(EntityType.COMPONENT, "黄芪甲苷"),  # 化学成分
        end=HERB("黄芪"),  # 所属药材
        content_reference="药典2020 一部",  # 含量测定参照
        provenance=prov,
    )


def test_manifests_valid(prov: Provenance) -> None:
    """疾病表现边：西医疾病→西医症状（高血压表现为头痛）。"""
    assert Manifests(
        start=_ref(EntityType.DISEASE_WM, "原发性高血压"),
        end=_ref(EntityType.SYMPTOM_WM, "头痛"),
        provenance=prov,
    )


def test_aliased_in_valid(prov: Provenance) -> None:
    """异名收录边：药→出处（"北芪"这一异名收录于《中医药学名词》）。"""
    assert AliasedIn(
        start=HERB("黄芪"), end=_ref(EntityType.SOURCE, "中医药学名词"), provenance=prov
    )


def test_registry_counts_match_design_doc() -> None:
    """文档 §3 共 20 行：19 个有效关系类（CORRESPONDS_TO 两行共享边类型）+ 1 个弃用。"""
    assert len(RELATION_MODELS) == 19  # 关系模型类共 19 个
    assert len(ACTIVE_RELATION_TYPES) == 18  # 有效边类型 18 种（对应病/症共享 CORRESPONDS_TO）
    assert "CORRESPONDS_TO" in ACTIVE_RELATION_TYPES  # 共享类型在册
    assert frozenset({"HAS_INGREDIENT_ROLE"}) == DEPRECATED_RELATION_TYPES  # 唯一弃用项
    assert not (ACTIVE_RELATION_TYPES & DEPRECATED_RELATION_TYPES)  # 有效/弃用无交集
