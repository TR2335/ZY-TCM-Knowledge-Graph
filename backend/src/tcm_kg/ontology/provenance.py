"""Provenance 四元组（ontology-design.md §5）：所有节点与关系统一携带。"""


# 溯源四元组的pydantic 定义层
#

from datetime import UTC, datetime  # 标准库：UTC 时区与日期时间类型

from pydantic import BaseModel, ConfigDict, Field  # Pydantic：模型/配置/字段定义

from tcm_kg.ontology.enums import ProvenanceMethod  # 获取方式枚举（置信度约定）


# 溯源四元组：source_id（指向 Source 节点）/ confidence（0-1 置信度）/
# extracted_at（入库时间）/ method（获取方式）
class Provenance(BaseModel):
    """出处与置信度。

    - source_id 指向 Source 节点，加载时同步建立 FROM_SOURCE 关系；
    - confidence 约定见 ProvenanceMethod 文档字符串；
    - 同一事实多源冲突时保留全部边并在边属性记录各源，展示层按 confidence 排序。
    """

    model_config = ConfigDict(extra="forbid")  # 禁未定义属性，防 schema 漂移

    source_id: str = Field(  # 来源节点标识
        min_length=1,  # 非空：防悬空引用
        description="指向 Source 节点（同步建立 FROM_SOURCE 关系）",
    )
    confidence: float = Field(  # 多源冲突展示排序依据
        ge=0.0,  # 下界：不为负
        le=1.0,  # 上界：不超过 1
        description="置信度 0-1：curated=1.0 / db_import=0.95 / llm_extract=模型自评",
    )
    extracted_at: datetime = Field(  # 入库时间戳
        default_factory=lambda: datetime.now(UTC),  # 缺省取当前 UTC 时间
        description="入库时间（UTC）",
    )
    method: ProvenanceMethod = Field(description="获取方式")  # 枚举约束
