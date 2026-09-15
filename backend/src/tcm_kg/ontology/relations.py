"""20 种关系的 Pydantic 模型（ontology-design.md §3 零偏差）。

- 每个关系类携带 RELATION_TYPE（Cypher 中的边类型）与定义域/值域（START_LABELS/
  END_LABELS），构造时强制校验（§1 语义严格原则）。
- 各关系边属性中的 confidence 即 provenance.confidence（§5 统一四元组），不重复建模。
- CORRESPONDS_TO 在文档中为两行（疾病映射/症状映射），此处拆为两个类、共享同一
  RELATION_TYPE，便于类型化校验与 Cypher 复用。
- HAS_INGREDIENT_ROLE 已弃用（并入 COMPOSED_OF.role，§3.2）。
- 基数约束（如 PROCESSED_FROM 为 N:1）属跨实例约束，由加载器（C9）与质检（D1/D2）
  保证，不在单条记录校验范围。
"""

# ============================================================
# 文件：relations.py
# 用途：20 种关系的 Pydantic 模型定义层
# 所属层级：tcm_kg.ontology（本体层 —— 单一事实源代码化）
# 核心功能：每种关系对应图谱中的一条边类型（COMPOSED_OF/TREATS_PATTERN 等），
#          携带 RELATION_TYPE（Cypher 边类型）与定义域/值域（START_LABELS/END_LABELS），
#          构造时强制校验语义方向（§1 语义严格原则）
# 如何使用：被加载器实例化后写入 Neo4j；被 API 层用于边序列化校验；
#          通过 __init__.py 统一导出；schema 变更先改文档再改代码（§6）
# ============================================================


# 关系的pydantic 定义层

from typing import ClassVar, Literal, Self  # 标准库：类级常量/字面量/自身类型注解

from pydantic import BaseModel, ConfigDict, Field, model_validator  # Pydantic 基础组件

from tcm_kg.ontology.entities import EntityType  # 实体类型枚举：域校验用

# 本体枚举：配伍禁忌/剂量单位/配伍角色/妊娠分级/七情类型（import 行近 100 宽，故用上方注释）
from tcm_kg.ontology.enums import ContraType, DoseUnit, FormulaRole, PregnancyLevel, SynonymType
from tcm_kg.ontology.provenance import Provenance  # 溯源四元组：每条边统一携带（§5）

PREGNANCY_CATEGORY_NAME: str = "妊娠禁忌"  # 妊娠禁忌边的目标节点名（双轨建模）
"""PREGNANCY_CONTRA 边的目标 Category 节点名（§3：双轨建模——属性 + 可查询边）。"""


# 节点引用模型：一条边的起点/终点，由 label（实体类型）+ name（规范名）唯一标识
class NodeRef(BaseModel):
    """节点引用：一条边的起点/终点（label + 规范名）。"""

    model_config = ConfigDict(extra="forbid")  # 禁未定义属性，防 schema 漂移

    label: EntityType  # 实体类型（图中 Label）
    name: str = Field(min_length=1)  # 规范名：与 label 联合定位节点


class RelationBase(BaseModel):
    """关系公共基类：起止节点 + provenance + 定义域/值域校验。"""

    model_config = ConfigDict(extra="forbid")  # 禁未定义属性，防 schema 漂移

    RELATION_TYPE: ClassVar[str] = ""  # Cypher 边类型名（子类必须覆写）
    START_LABELS: ClassVar[frozenset[EntityType]] = frozenset()  # 定义域：合法起点集合
    END_LABELS: ClassVar[frozenset[EntityType]] = frozenset()  # 值域：合法终点集合

    start: NodeRef  # 边起点节点引用
    end: NodeRef  # 边终点节点引用
    provenance: Provenance  # 溯源四元组（§5）

    @model_validator(mode="after")  # 构造后自动执行（§1 语义严格原则）
    def _validate_domain_range(self) -> Self:
        if self.START_LABELS and self.start.label not in self.START_LABELS:  # 起点域校验
            raise ValueError(
                f"{type(self).RELATION_TYPE} 定义域违规：起点 {self.start.label.value} 不在 "
                f"{_labels_repr(self.START_LABELS)} 中"
            )
        if self.END_LABELS and self.end.label not in self.END_LABELS:  # 终点域校验
            raise ValueError(
                f"{type(self).RELATION_TYPE} 值域违规：终点 {self.end.label.value} 不在 "
                f"{_labels_repr(self.END_LABELS)} 中"
            )
        return self  # 校验通过原样返回


# 辅助函数：将 frozenset[EntityType] 格式化为 "Label1 | Label2" 形式的可读字符串（用于报错信息）
def _labels_repr(labels: frozenset[EntityType]) -> str:
    return " | ".join(sorted(label.value for label in labels))  # 排序拼接保证报错可读


class ComposedOf(RelationBase):
    """COMPOSED_OF：Formula → Herb（方剂组成，剂量与角色）。"""

    RELATION_TYPE = "COMPOSED_OF"  # Cypher 边类型：方剂组成
    START_LABELS = frozenset({EntityType.FORMULA})  # 定义域：起点须为方剂
    END_LABELS = frozenset({EntityType.HERB})  # 值域：终点须为中药

    # gt=0：折算后剂量须为正数
    dose: float | None = Field(default=None, gt=0, description="折算后剂量（1钱≈3g/1两≈30g，D2）")
    unit: DoseUnit | None = Field(default=None, description="剂量单位")  # 与 dose 配套
    raw_dose: str | None = Field(default=None, description="古典单位原文，如 三两")  # 存原文备查
    role: FormulaRole | None = Field(default=None, description="君/臣/佐/使")  # 君臣佐使配伍角色


class HasEffect(RelationBase):
    """HAS_EFFECT：Herb/Formula → Effect（具有功效）。"""

    RELATION_TYPE = "HAS_EFFECT"  # Cypher 边类型：具有功效
    START_LABELS = frozenset({EntityType.HERB, EntityType.FORMULA})
    END_LABELS = frozenset({EntityType.EFFECT})

    source_text: str | None = Field(default=None, description="原文依据")


class TreatsPattern(RelationBase):
    """TREATS_PATTERN：Formula/Herb → Pattern（主治证候，辨证论治核心链路）。"""

    RELATION_TYPE = "TREATS_PATTERN"  # Cypher 边类型：主治证候
    START_LABELS = frozenset({EntityType.FORMULA, EntityType.HERB})
    END_LABELS = frozenset({EntityType.PATTERN})


class TreatsDisease(RelationBase):
    """TREATS_DISEASE：Formula/Herb → DiseaseTCM（主治中医疾病）。"""

    RELATION_TYPE = "TREATS_DISEASE"  # Cypher 边类型：主治中医疾病
    START_LABELS = frozenset({EntityType.FORMULA, EntityType.HERB})
    END_LABELS = frozenset({EntityType.DISEASE_TCM})


# 体现治法关系：Formula → TreatmentMethod，描述方剂所体现的治疗法则
class IndicatesMethod(RelationBase):
    """INDICATES_METHOD：Formula → TreatmentMethod（体现治法）。"""

    RELATION_TYPE = "INDICATES_METHOD"  # Cypher 边类型：体现治法
    START_LABELS = frozenset({EntityType.FORMULA})
    END_LABELS = frozenset({EntityType.TREATMENT_METHOD})


class HasSymptom(RelationBase):
    """HAS_SYMPTOM：Pattern/DiseaseTCM → SymptomTCM（证/病表现为症状）。

    is_primary 依据 GB/T 16751.2 主症/次症诊断标准（辨证加权用，ADR-012）。
    """

    RELATION_TYPE = "HAS_SYMPTOM"  # Cypher 边类型：表现为症状
    START_LABELS = frozenset({EntityType.PATTERN, EntityType.DISEASE_TCM})
    END_LABELS = frozenset({EntityType.SYMPTOM_TCM})

    is_primary: bool = Field(default=False, description="是否主症（GB/T 16751.2）")


# 缓解症状关系：Formula/Herb → SymptomTCM
class AllaysSymptom(RelationBase):
    """ALLAYS_SYMPTOM：Formula/Herb → SymptomTCM（缓解症状）。"""

    RELATION_TYPE = "ALLAYS_SYMPTOM"  # Cypher 边类型：缓解症状
    START_LABELS = frozenset({EntityType.FORMULA, EntityType.HERB})
    END_LABELS = frozenset({EntityType.SYMPTOM_TCM})


# 中西医疾病映射关系：DiseaseTCM → DiseaseWM，ICD-11 双编码
class CorrespondsToDisease(RelationBase):
    """CORRESPONDS_TO：DiseaseTCM → DiseaseWM（中西医疾病映射，ICD-11 双编码）。"""

    RELATION_TYPE = "CORRESPONDS_TO"  # Cypher 边类型：中西医疾病映射
    START_LABELS = frozenset({EntityType.DISEASE_TCM})
    END_LABELS = frozenset({EntityType.DISEASE_WM})

    mapping_source: str | None = Field(default=None, description="映射来源，如 SymMap")


class CorrespondsToSymptom(RelationBase):
    """CORRESPONDS_TO：SymptomTCM → SymptomWM（中西医症状映射，SymMap）。"""

    RELATION_TYPE = "CORRESPONDS_TO"  # Cypher 边类型：中西医症状映射（与疾病映射共享）
    START_LABELS = frozenset({EntityType.SYMPTOM_TCM})
    END_LABELS = frozenset({EntityType.SYMPTOM_WM})

    mapping_source: str | None = Field(default=None, description="映射来源，如 SymMap")


# 配伍禁忌关系：Herb → Herb，安全红线（十八反/十九畏），携带 type/verse
class ContraindicatedWith(RelationBase):
    """CONTRAINDICATED_WITH：Herb → Herb（配伍禁忌，安全红线：十八反/十九畏）。"""

    RELATION_TYPE = "CONTRAINDICATED_WITH"  # Cypher 边类型：配伍禁忌
    START_LABELS = frozenset({EntityType.HERB})
    END_LABELS = frozenset({EntityType.HERB})

    type: ContraType = Field(description="十八反/十九畏")
    verse: str | None = Field(default=None, description="歌诀原文")


class PregnancyContra(RelationBase):
    """PREGNANCY_CONTRA：Herb → Category('妊娠禁忌')（妊娠禁/忌/慎，双轨建模）。"""

    RELATION_TYPE = "PREGNANCY_CONTRA"  # Cypher 边类型：妊娠禁忌
    START_LABELS = frozenset({EntityType.HERB})
    END_LABELS = frozenset({EntityType.CATEGORY})

    level: PregnancyLevel = Field(description="禁用/忌用/慎用")

    @model_validator(mode="after")  # 构造后校验终点节点名
    def _end_is_pregnancy_category(self) -> Self:
        if self.end.name != PREGNANCY_CATEGORY_NAME:  # 终点名须为妊娠禁忌
            raise ValueError(
                f"PREGNANCY_CONTRA 终点必须是名为 {PREGNANCY_CATEGORY_NAME!r} 的 Category 节点，"
                f"实际为 {self.end.name!r}"
            )
        return self


# 七情增效关系：Herb → Herb，相须/相使（增效配伍）
class SynergisticWith(RelationBase):
    """SYNERGISTIC_WITH：Herb → Herb（七情增效：相须/相使）。"""

    RELATION_TYPE = "SYNERGISTIC_WITH"  # Cypher 边类型：七情增效
    START_LABELS = frozenset({EntityType.HERB})
    END_LABELS = frozenset({EntityType.HERB})

    type: Literal[SynonymType.MUTUAL_REINFORCEMENT, SynonymType.MUTUAL_ASSISTANCE] = Field(
        description="相须/相使"  # 仅限增效两类，制约走 RESTRAINS
    )


# 七情制约关系：Herb → Herb，相畏/相杀（制约/减毒配伍）
class Restrains(RelationBase):
    """RESTRAINS：Herb → Herb（七情制约：相畏/相杀）。"""

    RELATION_TYPE = "RESTRAINS"  # Cypher 边类型：七情制约
    START_LABELS = frozenset({EntityType.HERB})
    END_LABELS = frozenset({EntityType.HERB})

    type: Literal[SynonymType.MUTUAL_RESTRAINT, SynonymType.MUTUAL_DETOXICATION] = Field(
        description="相畏/相杀"  # 仅限制约两类，增效走 SYNERGISTIC_WITH
    )


# 炮制品溯源关系：Herb → Herb，N:1（如 熟地黄→地黄），携带 method
class ProcessedFrom(RelationBase):
    """PROCESSED_FROM：Herb → Herb（炮制品溯源，N:1，如 熟地黄→地黄，D1）。"""

    RELATION_TYPE = "PROCESSED_FROM"  # Cypher 边类型：炮制品溯源
    START_LABELS = frozenset({EntityType.HERB})
    END_LABELS = frozenset({EntityType.HERB})

    method: str | None = Field(default=None, description="炮制法")


class BelongsTo(RelationBase):
    """BELONGS_TO：Herb/Formula/Pattern/DiseaseTCM/Effect → Category（分类挂载）。"""

    RELATION_TYPE = "BELONGS_TO"  # Cypher 边类型：分类挂载
    START_LABELS = frozenset(
        {
            EntityType.HERB,
            EntityType.FORMULA,
            EntityType.PATTERN,
            EntityType.DISEASE_TCM,
            EntityType.EFFECT,
        }
    )
    END_LABELS = frozenset({EntityType.CATEGORY})


# 知识出处关系：除 Category/Source 外全部实体 → Source（provenance 节点化；
# §3.2 完备性：Source 只作值域）
class FromSource(RelationBase):
    """FROM_SOURCE：除 Category/Source 外全部 → Source（知识出处，provenance 节点化）。

    §3.2 完备性检查："Source 只作为值域"——Source 自身不得作为起点。
    """

    RELATION_TYPE = "FROM_SOURCE"  # Cypher 边类型：知识出处
    START_LABELS = frozenset(EntityType) - {EntityType.CATEGORY, EntityType.SOURCE}
    END_LABELS = frozenset({EntityType.SOURCE})


# 含有成分关系：Component → Herb，二阶段加载，携带 content_reference
class ContainedIn(RelationBase):
    """CONTAINED_IN：Component → Herb（含有成分，二阶段）。"""

    RELATION_TYPE = "CONTAINED_IN"  # Cypher 边类型：成分含于药材
    START_LABELS = frozenset({EntityType.COMPONENT})
    END_LABELS = frozenset({EntityType.HERB})

    content_reference: str | None = Field(default=None, description="含量文献依据")


class Manifests(RelationBase):
    """MANIFESTS：DiseaseWM → SymptomWM（西医疾病表现，SymMap）。"""

    RELATION_TYPE = "MANIFESTS"  # Cypher 边类型：西医疾病表现症状
    START_LABELS = frozenset({EntityType.DISEASE_WM})
    END_LABELS = frozenset({EntityType.SYMPTOM_WM})


# 别名著录出处关系：Herb/Formula → Source
class AliasedIn(RelationBase):
    """ALIASED_IN：Herb/Formula → Source（别名著录出处）。"""

    RELATION_TYPE = "ALIASED_IN"  # Cypher 边类型：别名著录出处
    START_LABELS = frozenset({EntityType.HERB, EntityType.FORMULA})
    END_LABELS = frozenset({EntityType.SOURCE})


RelationModel = type[RelationBase]  # 关系模型类类型别名，便于注解复用

# 全部 19 个关系模型类（对应 18 种 Cypher 边类型：两个 CORRESPONDS_TO 类共享同一类型名）
_RELATION_CLASSES: tuple[RelationModel, ...] = (
    ComposedOf,  # 方剂组成
    HasEffect,  # 具有功效
    TreatsPattern,  # 主治证候
    TreatsDisease,  # 主治中医疾病
    IndicatesMethod,  # 体现治法
    HasSymptom,  # 表现为症状
    AllaysSymptom,  # 缓解症状
    CorrespondsToDisease,  # 中西医疾病映射
    CorrespondsToSymptom,  # 中西医症状映射
    ContraindicatedWith,  # 配伍禁忌
    PregnancyContra,  # 妊娠禁忌
    SynergisticWith,  # 七情增效
    Restrains,  # 七情制约
    ProcessedFrom,  # 炮制品溯源
    BelongsTo,  # 分类挂载
    FromSource,  # 知识出处
    ContainedIn,  # 含有成分
    Manifests,  # 西医疾病表现
    AliasedIn,  # 别名著录出处
)

# 关系名 → 关系类的映射字典，供导出/校验/加载器按名查找关系模型
RELATION_MODELS: dict[str, RelationModel] = {cls.__name__: cls for cls in _RELATION_CLASSES}

ACTIVE_RELATION_TYPES: frozenset[str] = frozenset(cls.RELATION_TYPE for cls in _RELATION_CLASSES)
"""当前有效的 Cypher 边类型（18 种；文档 20 行 = 2 行 CORRESPONDS_TO + 1 行弃用）。"""

DEPRECATED_RELATION_TYPES: frozenset[str] = frozenset({"HAS_INGREDIENT_ROLE"})
"""已弃用边类型（HAS_INGREDIENT_ROLE 并入 COMPOSED_OF.role，§3.2）。"""
