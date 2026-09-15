# ===================================================================
# 文件：contracts.py
# 用途：在线 API 的稳定数据契约（v1 传输协议），与 Agent 内部实现解耦
# 所属层级：api 层（F 阶段 · 接口契约定义，被 app.py/retrieval/agent 引用）
# 核心功能：
#   - Evidence：可回溯证据（回答只能引用检索阶段产出的证据）
#   - SafetyDecision：安全策略的可解释输出（level/命中规则/掩码字段/需医师确认）
#   - ChatResponse/SearchResponse：v1 问答与检索响应（含 request_id 透传）
#   - FeedbackRequest/ErrorResponse：评估闭环反馈与统一错误结构
# 设计要点：
#   契约独立于实现——将来替换 LangGraph、向量库或前端时传输协议保持稳定。
# ===================================================================

"""在线 API 的稳定数据契约。

契约与 Agent 的内部实现解耦，方便以后替换 LangGraph、向量库或前端，
也让面试展示时可以明确说明“领域结果”和“传输协议”分别负责什么。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """一条可回溯证据，回答只能引用检索阶段产出的证据。"""

    evidence_id: str = Field(min_length=1, max_length=160)
    text: str = Field(default="", max_length=4000)
    evidence_type: Literal["entity", "relation", "source", "rule"] = "entity"
    source_id: str | None = None
    source_title: str | None = None
    locator: str | None = None
    entity_label: str | None = None
    entity_name: str | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SafetyDecision(BaseModel):
    """安全策略的可解释输出。"""

    level: Literal["safe", "caution", "blocked", "unknown"] = "unknown"
    rules: list[str] = Field(default_factory=list)
    masked_fields: list[str] = Field(default_factory=list)
    requires_clinician: bool = False
    user_message: str | None = None


class ChatResponse(BaseModel):
    """v1 问答响应；保留 safety_warning 兼容旧前端。"""

    answer: str
    request_id: str | None = None
    trace_id: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    safety: SafetyDecision = Field(default_factory=SafetyDecision)
    safety_warning: str | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)


class SearchResponse(BaseModel):
    """证据化检索响应。"""

    query: str
    results: list[Evidence] = Field(default_factory=list)
    request_id: str | None = None


class FeedbackRequest(BaseModel):
    """面向评估闭环的轻量反馈。"""

    request_id: str | None = Field(default=None, max_length=160)
    rating: Literal["helpful", "not_helpful"]
    grounded: bool | None = None
    safe: bool | None = None
    comment: str | None = Field(default=None, max_length=2000)


class ErrorResponse(BaseModel):
    """统一错误结构。"""

    code: str
    message: str
    request_id: str | None = None
