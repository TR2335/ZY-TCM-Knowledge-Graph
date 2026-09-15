# ===================================================================
# 文件：core/__init__.py
# 用途：核心基础设施包入口
# 所属层级：core 横切层（被各层引用，自身零业务依赖）
# 核心功能：聚合配置（settings）、结构化日志（logging）、链路追踪（tracing）
#          三大横切关注点，为业务层提供基础设施支撑
# 如何使用：通过 tcm_kg.core.settings.get_settings() 取配置，
#          tcm_kg.core.logging.setup_logging/get_logger 用日志，
#          tcm_kg.core.tracing.get_tracer() 取追踪客户端
# ===================================================================

"""core 横切层：settings / logging / tracing（被各层引用，自身零业务依赖）。"""
