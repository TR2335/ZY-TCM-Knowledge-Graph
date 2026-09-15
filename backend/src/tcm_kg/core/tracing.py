# ===================================================================
# 文件：tracing.py
# 用途：LLM 调用链路追踪，集成 Langfuse 记录 Agent 工具调用 trace
# 所属层级：core 横切层（B6.2/B6.3 · 可观测基础设施）
# 核心功能：
#   - get_tracer 返回已初始化的 Langfuse 客户端（进程级缓存）
#   - 未安装 langfuse SDK 或未配置环境变量时安全降级为 None，不阻断主流程
#   - LLM/Agent 调用统一埋点接入在 Phase E（E3）
# 如何使用：
#   tracer = get_tracer()；非 None 时用于记录 trace，None 时跳过追踪。
#   Langfuse 经 `make up-observability`（compose profile）启动，读取
#   LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 环境变量。
# ===================================================================

"""Langfuse 追踪（B6.2/B6.3）。

- Langfuse 服务通过 `make up-observability`（compose profile）启动；
- SDK 读取标准环境变量 LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY；
- 未安装或未配置时安全降级为 None，不阻断主流程；
- LLM 调用统一封装埋点在 Phase E（E3）接入。
"""

# 检查 langfuse 是否配置正确

import os  # 读取 Langfuse 相关环境变量
from functools import lru_cache  # 进程级缓存 tracer 单例
from typing import TYPE_CHECKING  # 仅类型检查期导入，无运行时依赖

from tcm_kg.core.logging import get_logger  # 复用统一日志设施记录降级事件

if TYPE_CHECKING:  # 类型检查专用分支
    from langfuse import Langfuse  # Langfuse 客户端类型（运行时不导入）

logger = get_logger(__name__)  # 模块级 logger，记录追踪降级事件


# 获取 Langfuse 客户端（lru_cache 单例）：未安装/未配置/初始化失败均降级为 None
@lru_cache(maxsize=1)
def get_tracer() -> "Langfuse | None":
    """返回已初始化的 Langfuse 客户端；未安装/未配置时返回 None（安全降级）。"""
    try:
        from langfuse import Langfuse  # 延迟导入，未安装走 ImportError 分支
    except ImportError:
        logger.info("langfuse_sdk_not_installed")  # 记录 SDK 缺失
        return None  # 降级为不追踪
    # 公钥或私钥任一缺失即视为未配置
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        logger.info("langfuse_not_configured_tracing_skipped")  # 记录未配置
        return None  # 降级为不追踪
    try:
        return Langfuse()  # 初始化客户端（自动读取环境变量）
    except Exception as exc:  # 初始化失败也安全降级
        logger.warning("langfuse_init_failed", error=str(exc))  # 记录失败原因
        return None  # 不阻断主流程


__all__ = ["get_tracer", "logger"]  # 导出追踪入口与模块 logger
