# ===================================================================
# 文件：retrieval/__init__.py
# 用途：GraphRAG 检索包入口
# 所属层级：retrieval 层（C10 · 在线混合检索）
# 核心功能：聚合混合检索编排器 HybridRetriever（三路召回 + RRF 融合）
#          与融合工具函数 reciprocal_rank_fusion，供 agent 工具层调用
# 如何使用：HybridRetriever(lexical=..., vector=..., graph=...).search(query)
# ===================================================================

"""GraphRAG 检索组件。"""

from .hybrid import HybridRetriever, reciprocal_rank_fusion  # 包级 re-export 检索组件

__all__ = ["HybridRetriever", "reciprocal_rank_fusion"]  # 声明包公开 API
