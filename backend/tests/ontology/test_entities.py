# ===================================================================
# 文件：backend/tests/ontology/test_entities.py（ontology 层单测 · 实体模型）
# 用途：对 12 类实体模型逐一验证——合法样例可构造、非法输入被拒（必填缺失/多余字段/枚举越界）
# 覆盖验收点：A8.4（模型样例合法/非法双向验证）+ §7.3 排除标记 + ADR-011 中成药建模
# ===================================================================

"""实体模型合法/非法样例单测（A8.4）。"""

import pytest  # 测试框架：raises 断言 + approx 浮点比较
from pydantic import ValidationError  # Pydantic 校验失败异常（非法输入应抛出）

from tcm_kg.ontology import (  # 被测对象：全部实体模型与注册表
    ENTITY_MODELS,  # 12 类实体的注册表（LABEL → 模型类）
    Category, Component, DiseaseTCM, DiseaseWM, Effect, EntityBase, Formula, Herb, Pattern,  # 实体模型
    Provenance, Source, SourceType, SymptomTCM, SymptomWM, TreatmentMethod,  # 溯源与来源
)
from tcm_kg.ontology.enums import (  # 实体字段用到的全部枚举
    DosageForm, Flavor, FormulaType, Meridian, Nature, PatternCategory, PregnancyLevel, SafetyLevel, Toxicity,
)


def _herb(prov: Provenance) -> Herb:
    """合法药材样例工厂：黄芪（字段最全的中药实体，多处复用）。"""
    return Herb(
        name="黄芪",  # 药名（必填）
        aliases=["北芪"],  # 异名列表（对齐与检索用）
        pinyin="huang qi",  # 拼音（BATMAN 拼音映射对齐用）
        nature=Nature.SLIGHTLY_WARM,  # 药性：微温（枚举）
        flavors=[Flavor.SWEET],  # 药味：甘（枚举列表）
        meridians=[Meridian.SPLEEN, Meridian.LUNG],  # 归经：脾经+肺经
        toxicity=Toxicity.NONE,  # 毒性：无毒
        dose_range="9-30g",  # 常用剂量范围（文本）
        pregnancy_level=PregnancyLevel.UNMARKED,  # 妊娠分级：未标注
        standard_ref="药典2020",  # 标准出处参照
        provenance=prov,  # 溯源四元组（conftest 夹具注入）
    )


def test_herb_valid(prov: Provenance) -> None:
    """合法药材：字段完整构造成功，枚举正确读入。"""
    herb = _herb(prov)  # 构造黄芪样例
    assert herb.name == "黄芪"  # 名称原样保存
    assert herb.nature == Nature.SLIGHTLY_WARM  # 药性枚举正确
    assert herb.meridians == [Meridian.SPLEEN, Meridian.LUNG]  # 归经列表保序保存
    assert herb.is_excluded_in_formula is False  # 排除标记默认 False（普通药材可入方剂）


def test_herb_excluded_marker(prov: Provenance) -> None:
    """§7.3：排除清单药材（如 生川乌）仍建节点，但带排除标记与毒性。"""
    herb = Herb(  # 构造"排除型"药材：只给最核心字段
        name="生川乌",  # 药名
        toxicity=Toxicity.HIGHLY_TOXIC,  # 大毒（毒性分级枚举）
        is_excluded_in_formula=True,  # 排除标记：L1 阶段据此禁止 COMPOSED_OF 边
        provenance=prov,  # 溯源（排除决策也可追溯）
    )
    assert herb.is_excluded_in_formula is True  # 标记生效
    # 毒性字段存在且触发"需要剂量掩码"（requires_dose_masking 属性联动安全防线）
    assert herb.toxicity is not None and herb.toxicity.requires_dose_masking


def test_herb_missing_name(prov: Provenance) -> None:
    """缺必填 name：报错信息必须提到 name 字段。"""
    with pytest.raises(ValidationError, match="name"):  # 断言抛错且报错含 "name"
        Herb(provenance=prov)  # type: ignore[call-arg]  # 故意不传 name


def test_herb_extra_field_forbidden(prov: Provenance) -> None:
    """多余字段：模型禁止额外字段（防拼写错误静默丢失数据）。"""
    with pytest.raises(ValidationError, match="extra_forbidden"):  # 报错应含 extra_forbidden
        Herb(name="黄芪", unexpected_field=1, provenance=prov)  # type: ignore[call-arg]  # 传入未定义字段


def test_herb_bad_nature(prov: Provenance) -> None:
    """枚举越界：药性只接受 Nature 枚举值，"超寒"应被拒。"""
    with pytest.raises(ValidationError):  # 非法枚举字符串 → 校验失败
        Herb(name="黄芪", nature="超寒", provenance=prov)  # type: ignore[arg-type]  # 故意传非法值


def test_formula_valid_and_safety_level_domain(prov: Provenance) -> None:
    """方剂合法构造 + safety_level 枚举域约束。"""
    formula = Formula(
        name="四逆汤",  # 方名
        source_dynasty="汉",  # 出处朝代
        source_book="伤寒论",  # 出处典籍
        efficacy="回阳救逆",  # 功效
        formula_type=FormulaType.CLASSIC,  # 类型：经典方
        safety_level=SafetyLevel.CAUTION,  # 含附子（有毒）→ caution（L2 推导结果）
        provenance=prov,
    )
    assert formula.safety_level == SafetyLevel.CAUTION  # 安全级别正确保存
    with pytest.raises(ValidationError):  # safety_level 只接受 SafetyLevel 枚举
        Formula(name="x", safety_level="high_risk", provenance=prov)  # type: ignore[arg-type]  # 自造值被拒


def test_patent_medicine_is_formula_attribute(prov: Provenance) -> None:
    """ADR-011：中成药以 formula_type 属性建模，复用全部关系。"""
    patent = Formula(  # 中成药不单独建实体类，仍是 Formula
        name="藿香正气水",  # 成药名
        formula_type=FormulaType.PATENT,  # 类型：中成药（属性区分，非类型区分）
        dosage_form=DosageForm.ORAL_LIQUID,  # 剂型：口服液
        manufacturer="示例药厂",  # 生产厂家（中成药特有字段）
        provenance=prov,
    )
    assert patent.formula_type == FormulaType.PATENT  # 建模方式验证通过


def test_disease_tcm_valid(prov: Provenance) -> None:
    """中医疾病：GB/T 编码与类目路径合法保存。"""
    disease = DiseaseTCM(name="感冒", code="A01", category_path="A01", provenance=prov)  # 国标 A 码实体
    assert disease.code == "A01"  # 编码正确


def test_pattern_valid(prov: Provenance) -> None:
    """证候：GB/T B 码 + 预定义类目枚举。"""
    pattern = Pattern(
        name="心脾两虚",  # 证候名
        code="B02.07.03",  # GB/T 证候码
        category_path=PatternCategory.ZANG_FU,  # 类目：脏腑证类（枚举，非自由文本）
        provenance=prov,
    )
    assert pattern.category_path == PatternCategory.ZANG_FU  # 类目正确


def test_symptom_tcm_valid(prov: Provenance) -> None:
    """中医症状：异名列表正确保存（如 心悸/心下悸）。"""
    symptom = SymptomTCM(name="心悸", aliases=["心下悸"], provenance=prov)  # 带异名构造
    assert symptom.aliases == ["心下悸"]  # 异名保存


def test_disease_wm_requires_icd11(prov: Provenance) -> None:
    """西医疾病：icd11_code 必填（缺失被拒）。"""
    valid = DiseaseWM(  # 合法样例：带 ICD-11 编码与英文名
        name="原发性高血压", icd11_code="BA00", en_name="Essential hypertension", provenance=prov
    )
    assert valid.icd11_code == "BA00"  # 编码正确
    with pytest.raises(ValidationError, match="icd11_code"):  # 缺编码 → 报错点名 icd11_code
        DiseaseWM(name="原发性高血压", provenance=prov)  # type: ignore[call-arg]  # 故意缺字段


def test_symptom_wm_valid(prov: Provenance) -> None:
    """西医症状：英文名字段正确保存。"""
    symptom = SymptomWM(name="头痛", en_name="Headache", provenance=prov)  # 中英对照症状
    assert symptom.en_name == "Headache"  # 英文名正确


def test_treatment_method_valid(prov: Provenance) -> None:
    """治法：level 只接受预定义值（"大法"可，"小法"拒）。"""
    method = TreatmentMethod(name="汗法", code="C01", level="大法", provenance=prov)  # 合法样例
    assert method.level == "大法"  # 级别正确
    with pytest.raises(ValidationError):  # 自造级别被拒
        TreatmentMethod(name="汗法", level="小法", provenance=prov)  # type: ignore[arg-type]


def test_effect_valid(prov: Provenance) -> None:
    """功效：名称+分类正确保存。"""
    effect = Effect(name="补气固表", category="补虚", provenance=prov)  # 功效实体
    assert effect.category == "补虚"  # 功效分类正确


def test_component_mw_positive(prov: Provenance) -> None:
    """成分：分子量必须为正数（负值被拒）。"""
    valid = Component(  # 合法成分：黄芪甲苷
        name="黄芪甲苷", pubchem_cid=1, molecular_formula="C41H68O14", mw=784.97, provenance=prov
    )
    assert valid.mw == pytest.approx(784.97)  # 浮点近似比较（避免精度误差）
    with pytest.raises(ValidationError):  # 负分子量 → 校验失败
        Component(name="黄芪甲苷", mw=-1.0, provenance=prov)


def test_source_valid(prov: Provenance) -> None:
    """出处实体：类型枚举 + 引用义务文本正确保存。"""
    source = Source(
        name="中华人民共和国药典（2020年版一部）",  # 来源名
        type=SourceType.PHARMACOPOEIA,  # 类型：药典
        citation="引用义务示例",  # 引用格式（引用义务）
        license="药典原文仅用于抽取与校验，不分发原文",  # 许可说明（合规红线）
        provenance=prov,
    )
    assert source.type == SourceType.PHARMACOPOEIA  # 类型正确


def test_category_requires_tree_path(prov: Provenance) -> None:
    """类目节点：tree_path 必填（缺失被拒）。"""
    valid = Category(name="妊娠禁忌", tree_path="SAFETY.01", level=1, provenance=prov)  # 合法类目
    assert valid.tree_path == "SAFETY.01"  # 树路径正确
    with pytest.raises(ValidationError, match="tree_path"):  # 缺路径 → 报错点名
        Category(name="妊娠禁忌", provenance=prov)  # type: ignore[call-arg]


def test_registry_complete_and_consistent() -> None:
    """12 类实体全部注册，且 LABEL 与注册键一致（§2）。"""
    assert len(ENTITY_MODELS) == 12  # 注册表数量恰为 12（新增/删除实体类型会立刻暴露）
    for label, model in ENTITY_MODELS.items():  # 遍历注册表逐项检查
        assert model.LABEL is label  # 模型类上的 LABEL 与注册键一致（防错位）
        assert issubclass(model, EntityBase)  # 所有注册项都是实体基类子类


def test_provenance_confidence_bounds() -> None:
    """溯源：置信度边界 [0,1] 闭区间合法，越界被拒；source_id 不许为空串。"""
    from tcm_kg.ontology import ProvenanceMethod  # 局部导入获取方式枚举

    assert Provenance(source_id="s", confidence=1.0, method=ProvenanceMethod.CURATED)  # 上界 1.0 合法
    assert Provenance(source_id="s", confidence=0.0, method=ProvenanceMethod.LLM_EXTRACT)  # 下界 0.0 合法
    with pytest.raises(ValidationError):  # 超 1 → 拒
        Provenance(source_id="s", confidence=1.5, method=ProvenanceMethod.LLM_EXTRACT)
    with pytest.raises(ValidationError):  # source_id 空串 → 拒（溯源必须可定位）
        Provenance(source_id="", confidence=0.5, method=ProvenanceMethod.CURATED)


def test_all_entity_types_constructible(prov: Provenance) -> None:
    """合法样例覆盖全部 12 类实体（每类至少一个可构造实例）。"""
    samples = {  # 按标签逐类构造最小合法实例
        "Herb": _herb(prov),  # 药材（复用工厂）
        "Formula": Formula(name="四逆汤", provenance=prov),  # 方剂
        "DiseaseTCM": DiseaseTCM(name="感冒", provenance=prov),  # 中医疾病
        "Pattern": Pattern(name="心脾两虚", provenance=prov),  # 证候
        "SymptomTCM": SymptomTCM(name="心悸", provenance=prov),  # 中医症状
        "DiseaseWM": DiseaseWM(name="原发性高血压", icd11_code="BA00", provenance=prov),  # 西医疾病
        "SymptomWM": SymptomWM(name="头痛", provenance=prov),  # 西医症状
        "TreatmentMethod": TreatmentMethod(name="汗法", provenance=prov),  # 治法
        "Effect": Effect(name="补气固表", provenance=prov),  # 功效
        "Component": Component(name="黄芪甲苷", provenance=prov),  # 成分
        "Source": Source(name="药典2020", type=SourceType.PHARMACOPOEIA, provenance=prov),  # 出处
        "Category": Category(name="妊娠禁忌", tree_path="SAFETY.01", provenance=prov),  # 类目
    }
    # 样例键集合与注册表完全一致（一类不漏）
    assert set(samples) == {label.value for label in ENTITY_MODELS}
