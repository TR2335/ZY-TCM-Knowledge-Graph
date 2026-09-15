"""tcm_kg.ontology —— 本体 Pydantic 模型（单一事实源代码化，A8）。

与 docs/ontology-design.md 零偏差；schema 变更流程：先改文档 → bump VERSION →
迁移脚本 → 质检回归（ontology-design.md §6）。
"""

# ============================================================
# 文件：__init__.py
# 用途：本体包入口
# 所属层级：tcm_kg.ontology（本体层 —— 单一事实源代码化）
# 核心功能：统一导出全部枚举、实体、关系和溯源模型，作为本体对外的单一导入入口；
#          读取 VERSION 文件暴露 ONTOLOGY_VERSION
# 如何使用：外部模块通过 from tcm_kg.ontology import Herb, Formula, ... 引用；
#          schema 变更流程：先改文档 → bump VERSION → 迁移脚本 → 质检回归（§6）
# ============================================================

from pathlib import Path  # 标准库：路径处理（读取 VERSION 文件）
from typing import Final  # 标准库：只读常量类型注解

from tcm_kg.ontology.entities import (  # 实体模型层：12 类实体 + 注册表
    ENTITY_MODELS,  # 实体类型 → 模型类注册表
    Category,  # 分类（导航节点）
    Component,  # 化学成分（二阶段预留）
    DiseaseTCM,  # 中医疾病
    DiseaseWM,  # 西医疾病
    Effect,  # 功效
    EntityBase,  # 实体公共基类
    EntityType,  # 实体 Label 枚举（12 类）
    Formula,  # 方剂
    Herb,  # 中药（饮片级）
    Pattern,  # 证候
    Source,  # 知识来源出处
    SymptomTCM,  # 中医症状
    SymptomWM,  # 西医症状
    TreatmentMethod,  # 治法
)
from tcm_kg.ontology.enums import (  # 枚举层：15 类规范术语约束
    ContraType,  # 配伍禁忌类型（十八反/十九畏）
    DosageForm,  # 剂型
    DoseUnit,  # 剂量单位
    Flavor,  # 药味
    FormulaRole,  # 君臣佐使配伍角色
    FormulaType,  # 方剂类型（经方/时方/中成药）
    Meridian,  # 归经（十二经）
    Nature,  # 药性（八级）
    PatternCategory,  # 证候分类（8 类）
    PregnancyLevel,  # 妊娠禁忌分级
    ProvenanceMethod,  # 知识获取方式
    SafetyLevel,  # 方剂安全分级
    SourceType,  # 知识来源类型
    SynonymType,  # 七情配伍类型
    Toxicity,  # 毒性分级
)
from tcm_kg.ontology.export import export_json_schemas  # JSON Schema 导出函数
from tcm_kg.ontology.provenance import Provenance  # 溯源四元组模型
from tcm_kg.ontology.relations import (  # 关系模型层：18 种边类型
    ACTIVE_RELATION_TYPES,  # 当前有效边类型集合
    DEPRECATED_RELATION_TYPES,  # 已弃用边类型集合
    PREGNANCY_CATEGORY_NAME,  # 妊娠禁忌分类节点名常量
    RELATION_MODELS,  # 关系名 → 模型类注册表
    AliasedIn,  # 别名著录出处
    AllaysSymptom,  # 缓解症状
    BelongsTo,  # 分类挂载
    ComposedOf,  # 方剂组成
    ContainedIn,  # 成分含于药材
    ContraindicatedWith,  # 配伍禁忌（安全红线）
    CorrespondsToDisease,  # 中西医疾病映射
    CorrespondsToSymptom,  # 中西医症状映射
    FromSource,  # 知识出处
    HasEffect,  # 具有功效
    HasSymptom,  # 证/病表现为症状
    IndicatesMethod,  # 方剂体现治法
    Manifests,  # 西医疾病表现症状
    NodeRef,  # 节点引用（边起止点）
    PregnancyContra,  # 妊娠禁忌
    ProcessedFrom,  # 炮制品溯源
    RelationBase,  # 关系公共基类
    Restrains,  # 七情制约（相畏/相杀）
    SynergisticWith,  # 七情增效（相须/相使）
    TreatsDisease,  # 主治中医疾病
    TreatsPattern,  # 主治证候
)

ONTOLOGY_VERSION: Final[str] = (  # 从包内 VERSION 文件读取的 semver 版本
    (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip()
)
"""本体 semver 版本（与 ontology-design.md 修订记录同步；M0 冻结后升 1.0）。"""

__all__ = [
    "ONTOLOGY_VERSION",
    # 实体
    "ENTITY_MODELS",
    "EntityBase",
    "EntityType",
    "Herb",
    "Formula",
    "DiseaseTCM",
    "Pattern",
    "SymptomTCM",
    "DiseaseWM",
    "SymptomWM",
    "TreatmentMethod",
    "Effect",
    "Component",
    "Source",
    "Category",
    # 关系
    "RELATION_MODELS",
    "ACTIVE_RELATION_TYPES",
    "DEPRECATED_RELATION_TYPES",
    "RelationBase",
    "NodeRef",
    "PREGNANCY_CATEGORY_NAME",
    "ComposedOf",
    "HasEffect",
    "TreatsPattern",
    "TreatsDisease",
    "IndicatesMethod",
    "HasSymptom",
    "AllaysSymptom",
    "CorrespondsToDisease",
    "CorrespondsToSymptom",
    "ContraindicatedWith",
    "PregnancyContra",
    "SynergisticWith",
    "Restrains",
    "ProcessedFrom",
    "BelongsTo",
    "FromSource",
    "ContainedIn",
    "Manifests",
    "AliasedIn",
    # 枚举
    "Nature",
    "Flavor",
    "Meridian",
    "Toxicity",
    "SafetyLevel",
    "PregnancyLevel",
    "FormulaRole",
    "FormulaType",
    "DosageForm",
    "DoseUnit",
    "SynonymType",
    "ContraType",
    "PatternCategory",
    "SourceType",
    "ProvenanceMethod",
    # 其他
    "Provenance",
    "export_json_schemas",
]
