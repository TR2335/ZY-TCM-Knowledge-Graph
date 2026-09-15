# ===== 文件注释块 =====
# 用途：解析器基类，定义统一解析接口，所有数据源解析器继承此类
# 所属层级：pipeline/parsers（C3 解析归一管线核心抽象层）
# 核心功能：定义 SourceParser 抽象基类；统一中间格式 envelope（entity/relation/quarantine）；
#          行级安全转换 iter_safe（ValidationError 坏行进隔离清单不阻断整源解析）；
#          DataFrame 清洗 records、NodeRef 片段构造 herboname 等。
# =====================
"""SourceParser 抽象（C3.1）：统一中间格式与隔离机制。

中间格式（JSONL 每行一个 envelope）：
    {"kind": "entity",    "label": "Herb",        "record": {...}}
    {"kind": "relation",  "type": "COMPOSED_OF",  "record": {...}}
    {"kind": "quarantine","source": "...",        "reason": "...", "raw": {...}}

- entity/relation 的 record 来自 A8 本体模型 model_dump(mode="json")，
  构造即校验（Pydantic），校验失败的原始行进隔离清单（C3 验收：100% 通过才入库）；
- 每个 parser 首条输出自身的 Source 实体（FROM_SOURCE 由加载器补建）；
- provenance 约定：db_import=0.95 / curated=1.0（enums.ProvenanceMethod 文档）。
"""

from abc import ABC, abstractmethod  # ABC 抽象基类与抽象方法装饰器
from collections.abc import Callable, Iterable, Iterator  # 集合抽象类型（生成器/回调注解用）
from typing import Any, ClassVar, cast  # 任意类型/类变量注解/类型断言

import pandas as pd  # DataFrame 读取与清洗（Excel/CSV 源表）
from pydantic import ValidationError  # Pydantic 校验异常（坏行隔离依据）

from tcm_kg.ontology import (
    EntityBase,  # 实体模型基类（A8 本体）
    EntityType,  # 实体类型枚举（Label 值来源）
    Provenance,  # 溯源模型（source_id+方法+置信度）
    ProvenanceMethod,  # 溯源方法枚举（db_import/curated）
    RelationBase,  # 关系模型基类（A8 本体）
    Source,  # 数据源实体模型（Source 节点）
    SourceType,  # 数据源类型枚举
)

Envelope = dict[str, Any]  # 统一中间格式信封类型别名（entity/relation/quarantine）


def entity_envelope(model: EntityBase) -> Envelope:
    """实体 → envelope（label 用于加载器选择 MERGE 目标 Label）。"""
    # 组装实体信封：label 取实体类 LABEL 枚举值；record 以 JSON 兼容模式导出（可序列化）
    return {"kind": "entity", "label": model.LABEL.value, "record": model.model_dump(mode="json")}


def relation_envelope(model: RelationBase) -> Envelope:
    """关系 → envelope（type 为 Cypher 边类型）。"""
    return {
        "kind": "relation",  # 信封种类标识：关系
        "type": type(model).RELATION_TYPE,  # 关系类型（加载器作 Cypher 边类型）
        "record": model.model_dump(mode="json"),  # 关系记录（JSON 兼容模式导出）
    }


def quarantine_envelope(source_id: str, reason: str, raw: object) -> Envelope:
    """校验失败的原始行 → 隔离 envelope（不阻断整体解析）。"""
    # reason 截断至 500 字符，防止超长异常消息导致 JSONL 行膨胀
    return {"kind": "quarantine", "source": source_id, "reason": reason[:500], "raw": raw}


class SourceParser(ABC):
    """一个冷启动数据源的解析器。

    子类约定：
    - source_id 即 Provenance.source_id（指向 Source 节点）；
    - parse() 为生成器，逐行产出 envelope；行级 ValidationError 自行捕获为
      quarantine envelope，不得让单行坏数据中断整源解析。
    """

    source_id: ClassVar[str]  # 溯源 ID（Provenance.source_id 指向的 Source 节点）
    source_name: ClassVar[str]  # 数据源显示名（Source 节点 name）
    source_type: ClassVar[SourceType] = SourceType.DATABASE  # 数据源类型，默认数据库
    citation: ClassVar[str | None] = None  # 引用文献（答案溯源的引用义务依据）
    license_note: ClassVar[str | None] = None  # 许可说明（版权合规记录）

    # 抽象解析方法：子类实现，逐行产出 envelope 流（首个应为 source_entity）
    @abstractmethod
    def parse(self, raw_dir: Any) -> Iterator[Envelope]:
        """产出 envelope 流（首个应为 source_entity）。"""

    def source_entity(self) -> Source:
        """本源的 Source 节点（答案溯源与引用义务依据）。"""
        return Source(
            name=self.source_name,  # 数据源名称
            type=self.source_type,  # 数据源类型
            citation=self.citation,  # 引用文献
            license=self.license_note,  # 许可说明
            provenance=self._prov(),  # 默认溯源：db_import，置信度 0.95
        )

    def _prov(
        self,
        method: ProvenanceMethod = ProvenanceMethod.DB_IMPORT,  # 溯源方法，默认库导入
        confidence: float = 0.95,  # 置信度默认 0.95（db_import 约定值）
    ) -> Provenance:
        # 构造溯源记录：绑定本源 source_id 与方法/置信度
        return Provenance(source_id=self.source_id, confidence=confidence, method=method)


def iter_safe(
    source_id: str,
    rows: Iterable[dict[str, Any]],
    convert: Callable[[dict[str, Any]], Iterable[Envelope]],
) -> Iterator[Envelope]:
    """逐行安全转换：ValidationError → quarantine，坏行不中断。"""
    for raw_row in rows:  # 逐行消费原始数据
        try:
            yield from convert(raw_row)  # 正常行：转换并产出 envelope 流
        except ValidationError as exc:  # 坏行：Pydantic 校验失败
            yield quarantine_envelope(source_id, str(exc), raw_row)  # 进隔离清单，不中断整源


def records(df: "pd.DataFrame") -> list[dict[str, Any]]:
    """DataFrame → 行 dict 列表（NaN→None；类型归一供 iter_safe 消费）。"""
    cleaned = df.where(pd.notna(df), None)  # NaN → None（避免 NaN 污染下游 Pydantic 校验）
    return cast("list[dict[str, Any]]", cleaned.to_dict("records"))  # 转 dict 行列表并断言类型


# NodeRef 便捷构造：生成 start/end 关系两端 record 片段（label=HERB，name=药名）
def herboname(name: str) -> dict[str, Any]:
    """NodeRef 便捷构造（start/end 的 record 片段）。"""
    return {"label": EntityType.HERB.value, "name": name}  # label=HERB 供加载器定位节点
