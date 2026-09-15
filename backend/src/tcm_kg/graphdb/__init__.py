# ===================================================================
# 文件：graphdb/__init__.py
# 用途：图数据库包入口
# 所属层级：graphdb 层（C9/C10 · 数据访问层）
# 核心功能：聚合 Neo4j 仓储（repo.Neo4jRepo / build_repo_from_settings），
#          提供节点/关系 CRUD 与批量加载能力，供 agent 工具层与 pipeline 加载层使用
# 如何使用：通过 tcm_kg.graphdb.repo.build_repo_from_settings() 构造仓储实例
# ===================================================================

"""graphdb：Neo4j 仓储 + 向量索引（C9/C10）。"""

# 包内成员经子模块访问（暂未在包级 re-export）：
#   repo.Neo4jRepo —— Neo4j 仓储：节点/关系 CRUD、批量加载
#   repo.build_repo_from_settings —— 从全局配置构造仓储实例
