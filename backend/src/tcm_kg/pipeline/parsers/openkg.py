# ===== 文件注释块 =====
# 用途：OpenKG 174 首中医经典方剂结构化数据集解析器（配伍金标准）
# 所属层级：pipeline/parsers（C3 解析归一管线 OpenKG 数据源解析器）
# 核心功能：解析"方剂 × 药味"展开行（1249 行）产出 Formula 实体与 COMPOSED_OF 组成边，
#          含君臣佐使角色（FormulaRole）与药典标准化剂量（DoseUnit）——
#          本源为全图谱方剂配伍的金标准参照。
# =====================
"""OpenKG 174 方剂 parser（C3.5）→ Formula + COMPOSED_OF（配伍金标准）。

数据：中国中医科学院《174首中医经典方剂结构化数据集》（君臣佐使 + 药典标准化剂量）。
表结构为"方剂 × 药味"展开行（1249 行）：每行一味药，剂量可空（如蜂蜜作引）。
"""

from collections.abc import Iterator  # 迭代器类型注解（生成器产出 envelope 流）
from typing import Any, ClassVar  # 任意类型/类变量注解

import pandas as pd  # Excel 读取（174 方剂表为 xlsx）

from tcm_kg.ontology import (
    ComposedOf,  # 组成关系模型（Formula→Herb）
    DoseUnit,  # 剂量单位枚举
    EntityType,  # 实体类型枚举（NodeRef label 用）
    Formula,  # 方剂实体模型
    FormulaRole,  # 君臣佐使角色枚举
    NodeRef,  # 关系两端节点引用
    ProvenanceMethod,  # 溯源方法枚举
    SourceType,  # 数据源类型枚举
)
from tcm_kg.pipeline.parsers.base import (
    Envelope,  # 统一信封类型别名
    SourceParser,  # 解析器抽象基类
    entity_envelope,  # 实体信封构造函数
    iter_safe,  # 行级安全转换（坏行隔离）
    records,  # DataFrame → 行 dict 列表
    relation_envelope,  # 关系信封构造函数
)
from tcm_kg.pipeline.parsers.symmap import _clean  # 复用 SymMap 的单元格清洗函数

# 君臣佐使单字 → FormulaRole 角色枚举
_ROLES = {
    "君": FormulaRole.MONARCH,  # 君药（主攻病因）
    "臣": FormulaRole.MINISTER,  # 臣药（辅助君药）
    "佐": FormulaRole.ASSISTANT,  # 佐药（佐助/制约/反佐）
    "使": FormulaRole.ENVOY,  # 使药（引经/调和诸药）
}
# 剂量单位文本 → DoseUnit 枚举（中英文写法对照）
_UNITS = {
    "克": DoseUnit.GRAM,  # 克（中文）
    "g": DoseUnit.GRAM,  # 克（英文缩写）
    "毫克": DoseUnit.MILLIGRAM,  # 毫克（中文）
    "mg": DoseUnit.MILLIGRAM,  # 毫克（英文缩写）
    "ml": DoseUnit.MILLILITER,  # 毫升（英文缩写）
}


class OpenKGParser(SourceParser):
    """174 首经典方剂（OpenKG，配伍金标准）。"""

    source_id = "src-openkg"  # 溯源 ID（Provenance.source_id）
    source_name = "OpenKG 174首中医经典方剂结构化数据集"  # 数据源显示名
    source_type = SourceType.DATABASE  # 数据库类数据源
    citation = (  # 引用（中国中医科学院，创新工程项目）
        "李园白、杨阳. 174首中医经典方剂结构化数据集. OpenKG, 2025. "
        "（中国中医科学院创新工程 CI2021A00508）"
    )
    license_note = "OpenKG 共享协议（非商用）"  # 许可限制说明

    SUBDIR: ClassVar[str] = "openkg"  # 原始数据子目录
    FILE: ClassVar[str] = "openkg_174_formulas.xlsx"  # 方剂×药味展开表文件名

    def parse(self, raw_dir: Any) -> Iterator[Envelope]:
        yield entity_envelope(self.source_entity())  # 首个产出：本源 Source 实体
        path = raw_dir / self.SUBDIR / self.FILE  # 数据文件路径
        df = pd.read_excel(path)  # 读取方剂展开表（1249 行）
        rows = records(df)  # DataFrame → 行 dict（NaN→None）
        seen_formulas: set[str] = set()  # 已见方剂名（实体去重）
        for row in rows:  # 逐行处理
            # 单行包裹 iter_safe：Pydantic 校验失败即隔离该行，不中断整表
            yield from iter_safe(
                self.source_id, [row], lambda r: self._row_records(r, seen_formulas)
            )

    def _row_records(self, row: dict[str, Any], seen_formulas: set[str]) -> Iterator[Envelope]:
        formula_name = _clean(row.get("方剂名称"))  # 方剂名（源表"方剂名称"列）
        herb_name = _clean(row.get("中药名称规范后"))  # 药味规范名（"中药名称规范后"列）
        if not formula_name or not herb_name:  # 方剂名或药名缺失跳过
            return
        if formula_name not in seen_formulas:  # 方剂首次出现才建实体
            seen_formulas.add(formula_name)  # 标记已见
            yield entity_envelope(Formula(name=formula_name, provenance=self._prov()))
        # 每行一味药：产出一条 COMPOSED_OF 组成边（配伍金标准）
        yield relation_envelope(
            ComposedOf(
                start=NodeRef(label=EntityType.FORMULA, name=formula_name),  # 起点：方剂
                end=NodeRef(label=EntityType.HERB, name=herb_name),  # 终点：药味
                dose=_dose(row.get("核对后的中药剂量")),  # 剂量数值（克，可空）
                unit=_unit(row.get("核对后的剂量单位")),  # 剂量单位（查表映射）
                role=_role(row.get("地位（君臣佐使）")),  # 君臣佐使角色
                provenance=self._prov(method=ProvenanceMethod.DB_IMPORT, confidence=0.95),  # 溯源
            )
        )


# 剂量解析：转 float（克），空值/非法/非正数返回 None
def _dose(value: object) -> float | None:
    """剂量 → float（克）；空/非法 → None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):  # 空值/NaN
        return None  # 无剂量（如蜂蜜作引等无量药）
    try:
        result = float(str(value))  # str() 兼容 numpy 标量
    except (TypeError, ValueError):  # 非数值文本（如"适量"）
        return None  # 非法剂量降级为 None
    return result if result > 0 else None  # 非正数视为无效


def _unit(value: object) -> DoseUnit | None:
    text = _clean(value)  # 清洗为干净文本
    return _UNITS.get(text) if text else None  # 查表映射，未知单位返回 None


# 君臣佐使角色解析：单字映射到 FormulaRole 枚举，未知返回 None
def _role(value: object) -> FormulaRole | None:
    text = _clean(value)  # 清洗为干净文本
    return _ROLES.get(text) if text else None  # 查表映射，未知角色返回 None
