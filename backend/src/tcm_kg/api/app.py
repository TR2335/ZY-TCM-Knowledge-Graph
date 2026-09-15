# ===================================================================
# 文件：app.py
# 用途：FastAPI 服务，提供 5 个 REST 端点（健康检查/问答/药材/方剂/图谱可视化）
# 所属层级：api 层（F 阶段 · 垂直切片服务出口）
# 核心功能：
#   - /api/health 健康检查、/api/chat 问答、/api/herb/{name} 药材、/api/formula/{name} 方剂
#   - /api/graph 图谱可视化数据（节点 + 边，限量）
#   - F3 双点掩码：所有出口再过一次 sanitize（API 层第二道消毒，工具层遗漏也不泄露剂量）
#   - 前端静态托管（同源访问，避免 file:// 跨域）
# 如何使用：
#   create_app(tools, agent) 注入工具与 Agent 构造应用；create_default_app() 从配置装配
#   Neo4j repo + LLM（可用时）/规则 Agent；CLI `tcm serve` 启动 uvicorn。
# ===================================================================

"""F FastAPI 最小服务（垂直切片）：问答 + 实体查询 + 图谱可视化数据。

F3 序列化掩码（ADR-013）：所有出口再过一次 sanitize（双点掩码的 API 侧），
即使工具层遗漏也不泄露剂量。
"""

import json
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from tcm_kg.agent.graph import RuleBasedAgent, ToolLoopAgent
from tcm_kg.agent.sanitizer import sanitize_formula, sanitize_herb
from tcm_kg.agent.tools import AgentTools
from tcm_kg.api.contracts import (
    ChatResponse as V1ChatResponse,
)
from tcm_kg.api.contracts import (
    ErrorResponse,
    FeedbackRequest,
    SafetyDecision,
    SearchResponse,
)
from tcm_kg.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


class ChatRequest(BaseModel):
    """问答请求。"""

    question: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    """问答响应（含安全警示透传）。"""

    answer: str
    safety_warning: str | None = None


def create_app(
    tools: AgentTools,
    agent: ToolLoopAgent | RuleBasedAgent | None = None,
) -> FastAPI:
    """应用工厂（tools/agent 注入；测试用 Fake）。"""
    app = FastAPI(title="本草通 TCM AI Knowledge Platform", version="1.0.0")
    setup_logging(dev=True)
    if agent is None:
        agent = RuleBasedAgent(tools)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:8000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, exc: ValueError) -> JSONResponse:
        request_id = getattr(_request.state, "request_id", None)
        body = ErrorResponse(code="VALIDATION_ERROR", message=str(exc), request_id=request_id)
        return JSONResponse(status_code=400, content=body.model_dump())

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health")
    async def v1_health(request: Request) -> dict[str, str | None]:
        return {"status": "ok", "service": "tcm-kg-api", "request_id": request.state.request_id}

    @app.get("/api/v1/ready")
    async def v1_ready(request: Request) -> JSONResponse:
        ready = tools.ready()
        payload = {
            "status": "ready" if ready else "not_ready",
            "dependencies": {"neo4j": "up" if ready else "down"},
            "request_id": request.state.request_id,
        }
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    # 问答端点：调用 Agent.answer，检测毒性警示并随响应透传
    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        answer = await agent.answer(req.question)
        warning = None
        if "执业中医师" in answer and "不提供剂量" in answer:
            warning = "毒性内容警示已随答案给出"
        return ChatResponse(answer=answer, safety_warning=warning)

    @app.post("/api/v1/chat", response_model=V1ChatResponse)
    async def v1_chat(req: ChatRequest, request: Request) -> V1ChatResponse:
        started = time.perf_counter()
        answer = await agent.answer(req.question)
        evidence = tools.search_evidence(req.question, limit=5)
        warning = (
            "毒性内容警示已随答案给出"
            if "执业中医师" in answer and "不提供剂量" in answer
            else None
        )
        safety = SafetyDecision(
            level="caution" if warning else "unknown",
            rules=["dose_masking"] if warning else [],
            masked_fields=["dose", "usage"] if warning else [],
            requires_clinician=bool(warning),
            user_message=warning,
        )
        return V1ChatResponse(
            answer=answer,
            request_id=request.state.request_id,
            safety_warning=warning,
            safety=safety,
            evidence=evidence,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @app.get("/api/v1/chat/stream")
    async def v1_chat_stream(
        request: Request,
        q: str = Query(min_length=1, max_length=2000),
    ) -> StreamingResponse:
        """SSE 流式协议：先发证据，再发最终答案，便于前端增量渲染。"""

        async def events() -> Any:
            evidence = tools.search_evidence(q, limit=5)
            payload = json.dumps([e.model_dump() for e in evidence], ensure_ascii=False)
            yield f"event: evidence\ndata: {payload}\n\n"
            answer = await agent.answer(q)
            payload = json.dumps(
                {"answer": answer, "request_id": request.state.request_id},
                ensure_ascii=False,
            )
            yield f"event: answer\ndata: {payload}\n\n"
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/v1/search", response_model=SearchResponse)
    async def v1_search(
        request: Request,
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=10, ge=1, le=100),
    ) -> SearchResponse:
        results = tools.search_evidence(q, limit=limit)
        return SearchResponse(query=q, results=results, request_id=request.state.request_id)

    @app.get("/api/v1/entities/{label}/{name}/neighbors")
    async def v1_neighbors(
        label: str,
        name: str,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return {
            "label": label,
            "name": name,
            "neighbors": tools.entity_neighbors(label, name, limit),
        }

    @app.get("/api/v1/entities/{label}/{name}")
    async def v1_entity(label: str, name: str) -> dict[str, Any]:
        if label == "Herb":
            detail = tools.get_herb(name)
        elif label == "Formula":
            detail = tools.get_formula(name)
        else:
            rows = tools.search_entities(name, limit=20)
            detail = next(
                (row for row in rows if row.get("label") == label and row.get("name") == name),
                None,
            )
        if detail is None:
            raise HTTPException(status_code=404, detail=f"实体不存在：{label}/{name}")
        return {"label": label, "name": name, "entity": detail}

    @app.get("/api/v1/stats")
    async def v1_stats(request: Request) -> dict[str, Any]:
        return {**tools.graph_stats(), "request_id": request.state.request_id}

    @app.post("/api/v1/feedback", status_code=202)
    async def v1_feedback(payload: FeedbackRequest, request: Request) -> dict[str, Any]:
        logger.info(
            "feedback_received",
            request_id=payload.request_id or request.state.request_id,
            rating=payload.rating,
        )
        return {"accepted": True, "request_id": request.state.request_id}

    @app.get("/api/herb/{name}")
    async def herb_detail(name: str) -> dict[str, Any]:
        """药材详情（F3：出口再消毒）。"""
        detail = tools.get_herb(name)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"药材不存在：{name}")
        return sanitize_herb(detail)  # 双点掩码（工具层 + API 层）

    # 方剂详情端点（F3：出口再消毒，含组成剂量双点掩码）
    @app.get("/api/formula/{name}")
    async def formula_detail(name: str) -> dict[str, Any]:
        """方剂详情（F3：出口再消毒）。"""
        detail = tools.get_formula(name)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"方剂不存在：{name}")
        return sanitize_formula(detail, detail.get("_components") or [])

    @app.get("/api/graph")
    async def graph(limit: int = Query(default=120, ge=1, le=2000)) -> dict[str, Any]:
        """图谱可视化数据（节点 + 边，限量）。"""
        return tools.graph_snapshot(limit)

    # 前端静态托管（同源访问，避免 file:// 跨域）：tcm-kgs/frontend/index.html
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    frontend_dir = Path(__file__).resolve().parents[4] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


# 生产工厂：从配置装配 Neo4j repo + LLM（可用时）/规则 Agent，LLM 不可用时降级
def create_default_app() -> FastAPI:
    """生产工厂：Neo4j repo + LLM（可用时）/规则 Agent。"""
    from tcm_kg.agent.graph import RuleBasedAgent, ToolLoopAgent
    from tcm_kg.graphdb.repo import build_repo_from_settings
    from tcm_kg.pipeline.llm import build_llm_from_settings

    repo = build_repo_from_settings()
    tools = AgentTools(repo, allow_raw_cypher=False)
    try:
        llm = build_llm_from_settings()
        if llm is None:
            raise ValueError("LLM 未配置")
        agent: ToolLoopAgent | RuleBasedAgent = ToolLoopAgent(llm, tools)
    except Exception as exc:  # noqa: BLE001 LLM 未配置 → 规则 Agent
        logger.warning("llm_unavailable_fallback_rule_agent", error=str(exc)[:200])
        agent = RuleBasedAgent(tools)
    return create_app(tools, agent)
