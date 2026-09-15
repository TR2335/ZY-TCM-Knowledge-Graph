# ===================================================================
# 文件：agent/__init__.py
# 用途：Agent 包入口
# 所属层级：agent 层（Agentic GraphRAG · E 阶段）
# 核心功能：聚合工具集（tools）、Agent 循环（graph）、安全消毒器（sanitizer），
#          提供 Agent 与图谱交互、多步工具调用与安全兜底能力
# 如何使用：通过 tcm_kg.agent.graph.ToolLoopAgent / RuleBasedAgent 构造问答 Agent，
#          通过 tcm_kg.agent.tools.AgentTools 获取六工具集
# ===================================================================

"""agent：Agentic GraphRAG（E 阶段：工具 + LangGraph 循环 + 安全兜底）。"""
