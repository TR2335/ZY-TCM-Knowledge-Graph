# ====================================================================
# 文件：llm.py
# 用途：LLM 构建工厂——从环境变量读取配置构建对话 LLM 实例
# 所属层级：tcm_kg.pipeline LLM 抽象层（供 ToolLoopAgent / extract.py 使用）
# 核心功能：
#   1. StructuredLLM 协议：按 Pydantic 模型抽取（extract）+ 纯文本对话（chat）
#   2. LangChainStructuredLLM：langchain-openai 实现，原生 Structured Outputs
#      （ADR-006：method="json_schema"；不兼容网关可退 "function_calling"）
#   3. ExtractionResult：抽取结果 + token 用量信息
#   4. build_llm_from_settings：从全局 Settings（TCM_LLM__* 环境变量）构造实例
# 如何使用：
#   from tcm_kg.pipeline.llm import build_llm_from_settings
#   llm = build_llm_from_settings()
#   result = await llm.extract(MyModel, system, user)
#   text = await llm.chat([{"role":"user","content":"..."}])
# ====================================================================
"""LLM 结构化输出客户端抽象（C4.2）。

- StructuredLLM：最小协议（按 Pydantic 模型抽取），测试注入 FakeLLM；
- LangChainStructuredLLM：OpenAI 兼容接口实现（ADR-006：原生 Structured Outputs，
  method="json_schema"；不兼容的网关可退 method="function_calling"）。
"""

from typing import Any, Protocol  # Any 通用类型 + Protocol 结构化协议

from pydantic import BaseModel  # 抽取结果数据模型基类


# 一次抽取调用结果 + 用量信息（data 为 Pydantic 模型实例，tokens 可缺失）
class ExtractionResult(BaseModel):
    """一次抽取调用结果 + 用量信息（可缺失）。"""

    data: BaseModel  # 抽取出的结构化数据（目标模型实例）
    tokens: int | None = None  # 本次调用 token 用量（网关未上报则 None）


# 结构化抽取协议：文本 + 目标 Pydantic 模型 → 模型实例（失败抛异常，重试由编排层负责）
class StructuredLLM(Protocol):
    """结构化抽取协议：文本 + 目标模型 → 模型实例。"""

    async def extract(self, model_cls: type[BaseModel], system: str, user: str) -> ExtractionResult:
        """执行一次抽取；失败抛异常（重试由编排层负责）。"""
        ...


# 基于 langchain-openai 的实现（ChatOpenAI 原生 structured output，OpenAI 兼容接口）
# settings 需配置 llm 分组（TCM_LLM__* 环境变量或 backend/.env）
class LangChainStructuredLLM:
    """基于 langchain-openai 的实现（ChatOpenAI 原生 structured output）。

    settings 需已配置 llm 分组（TCM_LLM__* 环境变量或 backend/.env）。
    """

    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = 0.1):
        from langchain_openai import ChatOpenAI  # 延迟导入（可选依赖，用到才加载）
        from pydantic import SecretStr  # 密钥包装类型（日志脱敏）

        self._llm = ChatOpenAI(  # 构建 OpenAI 兼容对话客户端
            base_url=base_url,  # 网关地址（OpenAI 兼容接口）
            api_key=SecretStr(api_key),  # 密钥经 SecretStr 包装防泄露
            model=model,  # 模型名（如 qwen/glm 等）
            temperature=temperature,  # 采样温度（默认 0.1 低随机性求稳）
        )

    # 按 Pydantic 模型抽取结构化输出（json_schema 方式）；解析失败抛 ValueError
    async def extract(self, model_cls: type[BaseModel], system: str, user: str) -> ExtractionResult:
        from langchain_core.messages import HumanMessage, SystemMessage  # 消息类型（延迟导入）

        # 绑定目标模型 schema（include_raw=True 保留原始响应以取 token 用量）
        runnable = self._llm.with_structured_output(model_cls, include_raw=True)
        result: dict[str, Any] = await runnable.ainvoke(  # 异步调用
            [SystemMessage(content=system), HumanMessage(content=user)]  # system + user 两条消息
        )
        data = result["parsed"]  # 解析成功的 Pydantic 实例（失败为 None）
        if data is None:  # 结构化输出解析失败
            # 抛错交上层重试（C4.5 失败处理）
            raise ValueError(f"LLM 未产出合法结构化输出：{result.get('parsing_error')}")
        raw = result.get("raw")  # 原始响应对象
        tokens = None  # 默认无 token 信息
        usage = getattr(raw, "usage_metadata", None)  # 提取用量元数据
        if isinstance(usage, dict):  # 网关上报了用量
            tokens = usage.get("total_tokens")  # 取总 token 数
        return ExtractionResult(data=data, tokens=tokens)  # 封装结果返回

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """纯文本对话（Agent 循环用）。"""
        from langchain_core.messages import (  # 消息类型（延迟导入）
            AIMessage,  # 助手消息
            BaseMessage,  # 消息基类（类型注解用）
            HumanMessage,  # 用户消息
            SystemMessage,  # 系统消息
        )

        lc_messages: list[BaseMessage] = []  # LangChain 消息列表
        for message in messages:  # 遍历 OpenAI 风格消息
            role, content = message["role"], message["content"]  # 取角色与内容
            if role == "system":  # system 角色
                lc_messages.append(SystemMessage(content=content))  # 转系统消息
            elif role == "assistant":  # assistant 角色
                lc_messages.append(AIMessage(content=content))  # 转 AI 消息
            else:  # 其余按 user 处理
                lc_messages.append(HumanMessage(content=content))  # 转用户消息
        result = await self._llm.ainvoke(lc_messages)  # 异步调用纯文本对话
        return str(result.content)  # 返回响应文本


# 从全局 Settings 构造 LLM 实例（入口 CLI 用；测试不经此函数，直接注入）
def build_llm_from_settings() -> LangChainStructuredLLM:
    """从全局配置构造（入口 CLI 用；测试不经过此函数）。"""
    from tcm_kg.core.settings import get_settings  # 全局配置（延迟导入）

    llm = get_settings().llm  # 读取 llm 配置组（TCM_LLM__* 环境变量）
    key = llm.api_key.get_secret_value().strip()  # 解出密钥明文并去空白
    if key.lower() in {"", "unset", "sk-your-key-here"}:  # 密钥未配置（占位值）
        raise ValueError("LLM API Key 未配置，使用规则 Agent 降级模式")  # 显式报错提示降级
    return LangChainStructuredLLM(  # 构造 LLM 客户端实例
        base_url=llm.base_url,  # 网关地址
        api_key=key,  # API 密钥
        model=llm.model,  # 模型名
        temperature=llm.temperature,  # 采样温度
    )
