"""12 类实体的 Pydantic 模型（ontology-design.md §2 零偏差）。

- 属性表、必填项、枚举约束均以设计文档为准；schema 变更先改文档再改代码（§6）。
- 所有实体统一携带 provenance（§5）。
- Herb.is_excluded_in_formula 为 L1 排除清单标记（§7.3）：排除清单药材仍建节点
  （毒性知识是安全教育内容），但不得出现在任何方剂组成中，且剂量/用法对外掩码。
- Formula.safety_level 由加载器按成分毒性推导（§7.1 L2），不手工填写：
  含"有毒"级药材 → caution；仅小毒/无毒 → safe。
"""

# ============================================================
# 文件：entities.py
# 用途：12 类实体的 Pydantic 模型定义层
# 所属层级：tcm_kg.ontology（本体层 —— 单一事实源代码化）
# 核心功能：每个实体对应知识图谱中的一个节点标签（Herb/Formula/DiseaseTCM 等），
#          定义属性表、必填项与枚举约束；所有实体统一携带 provenance（§5）
# 如何使用：被加载器实例化后写入 Neo4j；被 API 层用于序列化校验；
#          通过 __init__.py 统一导出；schema 变更先改文档再改代码（§6）
# ============================================================

#  12 类实体的 Pydantic 模型定义层 是整个中医知识图谱项目的核心数据结构定义文件。
# pydantic 定义层

from enum import StrEnum  # 标准库：字符串枚举基类（EntityType 用）
from typing import ClassVar, Literal  # 标准库：类级常量注解与字面量类型

from pydantic import BaseModel, ConfigDict, Field  # Pydantic：模型基类/配置/字段定义

from tcm_kg.ontology.enums import (  # 本体枚举：各实体字段的术语约束来源
    DosageForm,  # 剂型枚举
    Flavor,  # 药味枚举
    FormulaType,  # 方剂类型枚举
    Meridian,  # 归经枚举
    Nature,  # 药性枚举
    PatternCategory,  # 证候分类枚举
    PregnancyLevel,  # 妊娠禁忌分级枚举
    SafetyLevel,  # 方剂安全分级枚举
    SourceType,  # 知识来源类型枚举
    Toxicity,  # 毒性分级枚举
)
from tcm_kg.ontology.provenance import Provenance  # 溯源四元组模型（§5）


class EntityType(StrEnum):
    """实体 Label 总表（12 类，§2）。"""

    HERB = "Herb" # 中药（饮片级，§2.1） # § 是设计文档中的章节编号 引用对应的 2.1章节
    FORMULA = "Formula"  # 方剂（§2.2）
    DISEASE_TCM = "DiseaseTCM"  # 疾病（TCM 级别，§2.3）
    PATTERN = "Pattern"  # 证候（§4：8 类，排除韩医四象）
    SYMPTOM_TCM = "SymptomTCM" # 症状（TCM 级别，§2.4） TCM中医
    DISEASE_WM = "DiseaseWM" # 疾病（WM 级别，§2.5） WM西医
    SYMPTOM_WM = "SymptomWM" # 症状（WM 级别，§2.6）
    TREATMENT_METHOD = "TreatmentMethod" # 治疗方法（§2.7）
    EFFECT = "Effect" # 作用（§2.8）
    COMPONENT = "Component" # 组成（§2.9）
    SOURCE = "Source" # 知识来源（§2.11）
    CATEGORY = "Category" # 证候分类（§4：8 类，排除韩医四象）


class EntityBase(BaseModel):
    """实体公共基类：统一携带 provenance（§5）；禁止未定义属性（防 schema 漂移）。"""

    model_config = ConfigDict(extra="forbid")  # 禁未定义属性，防 schema 漂移

    LABEL: ClassVar[EntityType]  # 类级常量：图中节点 Label（子类必须覆写）

    provenance: Provenance  # 溯源四元组：所有节点统一携带（§5）


class Herb(EntityBase):
    """中药（饮片级，§2.1）。规范名以 2020 药典收载名为准（UNIQUE）。"""

    LABEL: ClassVar[EntityType] = EntityType.HERB  # 节点 Label：中药

    name: str = Field(min_length=1, description="规范名（UNIQUE）")  # 非空：图谱主键
    aliases: list[str] = Field(default_factory=list, description="异名/商品名/地方名，如 北芪→黄芪")
    pinyin: str | None = Field(default=None, description="拼音（检索辅助）")
    latin: str | None = Field(default=None, description="拉丁学名（nmm-data）")
    nature: Nature | None = Field(default=None, description="药性")  # 枚举约束：寒热温凉平等
    flavors: list[Flavor] = Field(default_factory=list, description="药味")
    meridians: list[Meridian] = Field(default_factory=list, description="归经")
    toxicity: Toxicity | None = Field(default=None, description="毒性分级")
    dose_range: str | None = Field(
        default=None, description="常用剂量区间（统一 g，古典单位按换算表折算）"
    )
    pregnancy_level: PregnancyLevel | None = Field(
        default=None, description="妊娠禁忌级别（药典注意项）"
    )
    icd11_code: str | None = Field(default=None, description="ICD-11 TM1 码（一般无）")
    standard_ref: str | None = Field(
        default=None, description="规范名依据（药典2020/中医药学名词/SymMap）"
    )
    is_excluded_in_formula: bool = Field(
        default=False,
        description="L1 排除清单标记（§7.3）：该药材（生品）不得出现在任何方剂组成中",
    )


# 方剂实体：≥2 味药组成的处方单元；字段含 source_book/efficacy/formula_type/
# dosage_form/safety_level 等（§2.2；中成药以 formula_type 属性建模，ADR-011）
class Formula(EntityBase):
    """方剂（§2.2）：≥2 味药组成的处方单元（含经方/时方/中成药）。

    中成药以 formula_type 属性建模，不设独立实体（ADR-011）。
    safety_level 由加载器按成分毒性推导（§7.1 L2）；高危方剂不获此属性——
    它们在 L1 数据层即被整体过滤，根本不入库（ADR-013）。
    """

    LABEL: ClassVar[EntityType] = EntityType.FORMULA  # 节点 Label：方剂

    name: str = Field(min_length=1, description="规范名（UNIQUE）")
    aliases: list[str] = Field(default_factory=list, description="别名")
    pinyin: str | None = Field(default=None, description="拼音（检索辅助）")
    source_dynasty: str | None = Field(default=None, description="出处朝代")
    source_book: str | None = Field(default=None, description="原书名（冗余便于展示）")
    efficacy: str | None = Field(default=None, description="功用摘要")
    usage: str | None = Field(default=None, description="用法（caution 方剂对外掩码，§7.4）")
    formula_type: FormulaType | None = Field(default=None, description="经方/时方/中成药")
    dosage_form: DosageForm | None = Field(default=None, description="剂型")
    manufacturer: str | None = Field(default=None, description="中成药厂家（可空）")
    safety_level: SafetyLevel | None = Field(
        default=None,
        description="安全分级（加载时由成分毒性推导：含有毒级药材→caution）",
    )


# 中医疾病实体：GB/T 15657 A 码标准名；核心字段含 name/code/icd11_code/category_path（§2.3）
class DiseaseTCM(EntityBase):
    """中医疾病（§2.3）：GB/T 15657 A 码标准名。"""

    LABEL: ClassVar[EntityType] = EntityType.DISEASE_TCM  # 节点 Label：中医疾病

    name: str = Field(min_length=1, description="GB/T 15657 标准名（UNIQUE）")
    code: str | None = Field(default=None, description="GB/T 15657 A 码")
    icd11_code: str | None = Field(default=None, description="ICD-11 TM1 码（如有）")
    category_path: str | None = Field(default=None, description="大类层级路径")


class Pattern(EntityBase):
    """证候（§2.4）：疾病某阶段病理概括，辨证论治的"证"（GB/T 15657 B 码）。"""

    LABEL: ClassVar[EntityType] = EntityType.PATTERN  # 节点 Label：证候

    name: str = Field(min_length=1, description="GB/T 15657 B 码标准名（UNIQUE）")
    code: str | None = Field(default=None, description="GB/T 15657 B 码")
    icd11_code: str | None = Field(default=None, description="ICD-11 TM1 码（SE70-SJ1Z）")
    category_path: PatternCategory | None = Field(
        default=None, description="证候分类（八纲/病因/气血津液/脏腑/经络/六经/三焦/卫气营血）"
    )


class SymptomTCM(EntityBase):
    """中医症状（§2.5）：中医口径的症状/体征表述（SymMap 收录）。"""

    LABEL: ClassVar[EntityType] = EntityType.SYMPTOM_TCM  # 节点 Label：中医症状

    name: str = Field(min_length=1, description="规范名（UNIQUE）")
    aliases: list[str] = Field(default_factory=list, description="别名")
    pinyin: str | None = Field(default=None, description="拼音（检索辅助）")


class DiseaseWM(EntityBase):
    """西医疾病（§2.6）：ICD-11 第 1-24 章。"""

    LABEL: ClassVar[EntityType] = EntityType.DISEASE_WM  # 节点 Label：西医疾病

    name: str = Field(min_length=1, description="规范名（UNIQUE）")
    icd11_code: str = Field(min_length=1, description="ICD-11 码（必填）")
    en_name: str | None = Field(default=None, description="英文名")


# 西医症状实体：现代医学症状；核心字段含 name/en_name（§2.7）
class SymptomWM(EntityBase):
    """西医症状（§2.7）：现代医学症状（SymMap 收录）。"""

    LABEL: ClassVar[EntityType] = EntityType.SYMPTOM_WM  # 节点 Label：西医症状

    name: str = Field(min_length=1, description="规范名（UNIQUE）")
    en_name: str | None = Field(default=None, description="英文名")


# 治法实体：治疗法则 GB/T 15657 C 码；核心字段含 name/code/level（§2.8）
class TreatmentMethod(EntityBase):
    """治法（§2.8）：治疗法则（GB/T 15657 C 码）。"""

    LABEL: ClassVar[EntityType] = EntityType.TREATMENT_METHOD  # 节点 Label：治法

    name: str = Field(min_length=1, description="治法名（UNIQUE）")
    code: str | None = Field(default=None, description="GB/T 15657 C 码")
    level: Literal["大法", "具体治法"] | None = Field(default=None, description="层级")


# 功效实体：中药/方剂的功效术语，归一到《中医药学名词》标准词表；核心字段含 name/category（§2.9）
class Effect(EntityBase):
    """功效（§2.9）：中药/方剂的功效术语（归一到《中医药学名词》标准词表）。"""

    LABEL: ClassVar[EntityType] = EntityType.EFFECT  # 节点 Label：功效

    name: str = Field(min_length=1, description="功效术语（UNIQUE）")
    category: str | None = Field(
        default=None, description="功效大类（补虚/解表/清热/活血等约 20 类，参照中药学教材）"
    )


# 化学成分实体：二阶段加载，schema 预留；核心字段含 name/pubchem_cid/molecular_formula/mw（§2.10）
class Component(EntityBase):
    """化学成分（§2.10）：二阶段加载，schema 预留。"""

    LABEL: ClassVar[EntityType] = EntityType.COMPONENT  # 节点 Label：化学成分

    name: str = Field(min_length=1, description="成分名（UNIQUE）")
    pubchem_cid: int | None = Field(default=None, description="PubChem CID")
    molecular_formula: str | None = Field(default=None, description="分子式")
    mw: float | None = Field(default=None, gt=0, description="分子量")  # gt=0：分子量为正


# 出处实体：知识来源（典籍/标准/药典/数据库/人工整理）；
# 字段含 name/type/citation/license（§2.11）
class Source(EntityBase):
    """出处（§2.11）：知识来源（典籍/标准/药典/数据库/人工整理）。"""

    LABEL: ClassVar[EntityType] = EntityType.SOURCE  # 节点 Label：出处

    name: str = Field(min_length=1, description="来源名（UNIQUE）")
    type: SourceType = Field(description="来源类型")  # 无默认值：必填枚举
    citation: str | None = Field(default=None, description="引用义务文本")
    license: str | None = Field(default=None, description="数据协议")


# 分类实体：树形分类导航节点，仅导航不承载业务知识；核心字段含 name/tree_path/level（§2.12）
class Category(EntityBase):
    """分类（§2.12）：树形分类导航节点（GB/T 15657 层级、功效大类等）。

    类目词不承载业务知识，仅导航；Category 之间不连业务关系（§3.2）。
    """

    LABEL: ClassVar[EntityType] = EntityType.CATEGORY  # 节点 Label：分类

    name: str = Field(min_length=1, description="分类名（UNIQUE）")
    tree_path: str = Field(min_length=1, description="树路径，如 B02.07.03")
    level: int | None = Field(default=None, ge=1, description="层级深度")  # ge=1：根层为 1 起


ENTITY_MODELS: dict[EntityType, type[EntityBase]] = {  # 实体类型 → 模型类注册表
    EntityType.HERB: Herb,  # 中药（饮片级）
    EntityType.FORMULA: Formula,  # 方剂
    EntityType.DISEASE_TCM: DiseaseTCM,  # 中医疾病
    EntityType.PATTERN: Pattern,  # 证候
    EntityType.SYMPTOM_TCM: SymptomTCM,  # 中医症状
    EntityType.DISEASE_WM: DiseaseWM,  # 西医疾病
    EntityType.SYMPTOM_WM: SymptomWM,  # 西医症状
    EntityType.TREATMENT_METHOD: TreatmentMethod,  # 治法
    EntityType.EFFECT: Effect,  # 功效
    EntityType.COMPONENT: Component,  # 化学成分
    EntityType.SOURCE: Source,  # 出处
    EntityType.CATEGORY: Category,  # 分类
}
