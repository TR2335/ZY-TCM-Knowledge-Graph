# ===================================================================
# 文件：logging.py
# 用途：结构化日志配置，使用 structlog 输出 JSON 格式日志
# 所属层级：core 横切层（B6.1 · 日志基础设施，被各层引用）
# 核心功能：
#   - setup_logging 初始化 structlog 并桥接标准 logging
#   - dev=True 彩色 pretty 输出（本地开发），dev=False JSON 行输出（生产采集）
#   - get_logger 获取模块级 logger，支持 contextvars 上下文合并
# 如何使用：
#   应用入口调用一次 setup_logging(dev=True)；模块内 logger = get_logger(__name__)
# ===================================================================

"""structlog 统一日志（B6.1）：开发态 pretty console、生产态 JSON，一行切换。

用法：
    from tcm_kg.core.logging import setup_logging, get_logger
    setup_logging(dev=True)          # 应用入口调用一次
    logger = get_logger(__name__)
"""

import logging  # 标准日志库，用于基础配置与级别常量
import sys  # 日志输出流指向标准输出
from typing import Any  # 类型标注（处理器/渲染器）

import structlog  # 结构化日志核心库

# 日志 配置 ：初始化 structlog 与标准 logging 桥接。

# 初始化 structlog 与标准 logging 桥接：dev 选 pretty/JSON 渲染，level 控制级别
def setup_logging(*, dev: bool = True, level: str = "INFO") -> None:
    """初始化 structlog 与标准 logging 桥接。

    - dev=True：彩色 pretty 输出（本地开发）；
    - dev=False：JSON 行输出（采集与检索友好，中文不转义）。
    """
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())  # 桥接标准日志
    level_value = getattr(logging, level.upper(), logging.INFO)  # 级别字符串转数值，缺省 INFO
    processors: list[Any] = [  # 日志处理器链（渲染前依次加工）
        structlog.contextvars.merge_contextvars,  # 合并请求级上下文（如 trace_id）
        structlog.processors.add_log_level,  # 附加日志级别字段
        structlog.processors.TimeStamper(fmt="iso", utc=True),  # 附加 ISO 格式 UTC 时间戳
        structlog.processors.StackInfoRenderer(),  # 渲染栈信息为可读文本
        structlog.dev.set_exc_info,  # 异常日志自动携带 exc_info
    ]
    renderer: Any = (  # 按运行模式选择渲染器
        structlog.dev.ConsoleRenderer()  # 开发态：彩色控制台输出
        if dev  # dev=True 时启用上一分支
        else structlog.processors.JSONRenderer(ensure_ascii=False)  # 生产态：JSON 行，中文不转义
    )
    structlog.configure(  # 应用全局 structlog 配置
        processors=[*processors, renderer],  # 处理器链 + 末尾渲染器
        wrapper_class=structlog.make_filtering_bound_logger(level_value),  # 按级别过滤
        logger_factory=structlog.PrintLoggerFactory(),  # 日志直接打印到标准输出
        cache_logger_on_first_use=True,  # 首次使用后缓存，提升性能
    )


# 获取 structlog logger（按模块名命名，模块级使用）
def get_logger(name: str) -> Any:
    """获取 structlog logger（模块级使用）。"""
    return structlog.get_logger(name)  # 按模块名创建/获取 logger
