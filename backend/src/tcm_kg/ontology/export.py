"""JSON Schema 导出（A8.4 自检产物）。"""

from typing import Any  # 标准库：任意类型（Schema 结构递归）

from tcm_kg.ontology.entities import ENTITY_MODELS  # 实体模型注册表
from tcm_kg.ontology.relations import RELATION_MODELS  # 关系模型注册表


# 导出全部实体与关系模型的 JSON Schema：实体键为 Label（如 "Herb"），关系键为类名（如 "ComposedOf"）
def export_json_schemas() -> dict[str, Any]:
    """导出全部实体/关系模型的 JSON Schema。

    键：实体为 Label（如 "Herb"），关系为类名（如 "ComposedOf"）。
    用途：C4 LLM 结构化抽取注入 schema、schema 漂移比对。
    """
    # entity_model
    # relation_model
    schemas: dict[str, Any] = {}  # 汇聚结果：schema 名 → JSON Schema
    for label, entity_model in ENTITY_MODELS.items():  # 遍历实体：键取 Label 值
        schemas[label.value] = entity_model.model_json_schema()  # 生成实体 Schema
    for name, relation_model in RELATION_MODELS.items():  # 遍历关系：键取类名
        schemas[name] = relation_model.model_json_schema()  # 生成关系 Schema
    return schemas  # 返回全部 schema（C4 注入/漂移比对用）
