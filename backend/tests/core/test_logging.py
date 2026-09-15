# ===================================================================
# 文件：backend/tests/core/test_logging.py（core 层单测 · 日志与追踪）
# 用途：验证两点——① 日志双模式（开发彩色 / 生产 JSON）切换正常 ② Langfuse 追踪未配置密钥时优雅降级
# 覆盖验收点：B6（追踪降级不抛异常）
# ===================================================================

"""日志与追踪降级单测（B6）。"""

import pytest  # 测试框架：monkeypatch 夹具

from tcm_kg.core.logging import get_logger, setup_logging  # 被测对象①：日志初始化与获取
from tcm_kg.core.tracing import get_tracer  # 被测对象②：追踪器工厂（带缓存）


def test_setup_logging_dev_and_json_modes() -> None:
    """日志双模式冒烟：dev 彩色模式与生产 JSON 模式都能正常输出（不抛错即通过）。"""
    setup_logging(dev=True)  # 开发模式：控制台彩色可读格式
    logger = get_logger("tcm.test")  # 按名称获取 logger（structlog 风格）
    logger.info("logging_ok", value=1)  # 结构化日志：消息 + 键值对（dev 模式下彩色渲染）
    setup_logging(dev=False, level="WARNING")  # 生产模式：JSON 单行输出，级别调到 WARNING
    logger.warning("json_mode_ok")  # JSON 模式下输出一条警告（应包含时间戳/级别/消息的 JSON 行）


def test_tracer_degrades_gracefully_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置 LANGFUSE_* 密钥时安全降级为 None，不抛异常。"""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)  # 清除公钥（不存在也不报错）
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)  # 清除私钥
    get_tracer.cache_clear()  # 清掉函数缓存：强制下次调用重新读环境变量（否则拿到上次的旧结果）
    assert get_tracer() is None  # 密钥缺失 → 返回 None（调用方拿 None 直接跳过追踪，绝不抛错）
