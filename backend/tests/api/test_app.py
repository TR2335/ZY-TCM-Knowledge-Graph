# ===================================================================
# 文件：backend/tests/api/test_app.py（api 层单测 · FastAPI 应用 F）
# 用途：验证全部 HTTP 端点——health/chat/herb/formula/graph——
#       以及 F3 双点掩码（API 出口二次消毒，工具层漏掉的这里兜住）
# 关键设计：TestClient 同步驱动 FastAPI + Fake 全注入（Repo/ChatLLM），零网络
# ===================================================================

"""F API 单测：端点 + F3 双点掩码（TestClient + Fake 全注入）。"""

from typing import Any  # 类型标注

import pytest  # 测试框架：fixture
from fastapi.testclient import TestClient  # FastAPI 官方测试客户端（不用真起服务器）

from tcm_kg.agent.graph import RuleBasedAgent, ToolLoopAgent  # 两种 Agent（按需注入）
from tcm_kg.agent.tools import AgentTools  # 六工具集
from tcm_kg.api.app import create_app  # 应用工厂（依赖注入入口）


class FakeRepo:
    """内存假图谱：按序返回预设查询结果（与 test_graph.py 同款模式）。"""

    def __init__(self) -> None:
        self._queue: list[list[dict[str, Any]]] = []  # 预设结果队列

    def enqueue(self, rows: list[dict[str, Any]]) -> None:
        """压入一批预设结果。"""
        self._queue.append(rows)  # 追加队列尾

    def run(self, query: str, **_params: Any) -> list[dict[str, Any]]:
        """弹出最早一批预设；队列空返回 []。"""
        if self._queue:  # 有预设
            return self._queue.pop(0)  # 先进先出
        return []  # 空结果


class FakeChatLLM:
    """脚本化对话 LLM：按序返回预设回复（模拟 tool-loop 的每一步）。"""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)  # 预设回复列表

    async def chat(self, _messages: list[dict[str, str]]) -> str:
        """弹出下一条预设；耗尽返回默认答案。"""
        if self._replies:  # 有预设
            return self._replies.pop(0)  # 按序消费
        return '{"answer": "默认回复"}'  # 耗尽兜底


@pytest.fixture
def repo() -> FakeRepo:
    """每个测试拿到独立假图谱。"""
    return FakeRepo()


def _client(repo: FakeRepo, llm: FakeChatLLM | None = None) -> TestClient:
    """组装测试客户端：注入 Fake 工具集 + Agent（有 LLM 用 tool-loop，无则规则）。"""
    tools = AgentTools(repo)  # 工具集绑定假图谱
    agent = ToolLoopAgent(llm, tools) if llm else RuleBasedAgent(tools)  # 按需选 Agent
    return TestClient(create_app(tools, agent))  # 应用工厂注入依赖 → 测试客户端


def test_health(repo: FakeRepo) -> None:
    """健康检查：固定返回 {"status": "ok"}。"""
    client = _client(repo)  # 组装客户端（规则 Agent 即可）
    assert client.get("/api/health").json() == {"status": "ok"}  # 探活端点返回固定负载


def test_chat_rule_agent(repo: FakeRepo) -> None:
    """问答端点：规则 Agent 模板回答（含警示）。"""
    repo.enqueue(  # 预设附子详情（规则路由「引号药材名」会查它）
        [
            {
                "name": "附子",
                "toxicity": "有毒",
                "dose_range": "3-15g",
                "nature": "热",
                "flavors": ["辛", "甘"],
                "meridians": ["心", "肾"],
                "effects": ["回阳救逆"],
            }
        ]
    )
    client = _client(repo)  # 规则 Agent 模式
    resp = client.post("/api/chat", json={"question": "查询「附子」详情"})  # POST 问答
    assert resp.status_code == 200  # 正常响应
    body = resp.json()  # 响应负载
    assert "【附子】" in body["answer"]  # 规则模板标题
    assert "执业中医师" in body["answer"]  # 警示透传到 HTTP 层
    assert "3-15g" not in body["answer"]  # 剂量已消毒（不出现在答案）


def test_herb_endpoint_double_mask(repo: FakeRepo) -> None:
    """F3 双点掩码：API 出口再次消毒（工具层已消毒的也复核）。

    即使工具层消毒逻辑未来被改坏，API 出口的第二道防线也能兜住——
    这就是"双点掩码"设计的意义。
    """
    repo.enqueue([{"name": "附子", "toxicity": "有毒", "dose_range": "3-15g", "effects": []}])  # 预设毒药记录
    client = _client(repo)  # 组装客户端
    resp = client.get("/api/herb/附子")  # GET 药材详情端点
    assert resp.status_code == 200  # 正常响应
    body = resp.json()  # 响应负载
    assert "3-15g" not in str(body)  # 整个负载（含嵌套）都无原始剂量
    assert body["dose_range"].startswith("【剂量信息已隐藏")  # 字段替换为掩码文案
    assert "执业中医师" in body["_safety_warning"]  # 附带标准警示


def test_herb_404(repo: FakeRepo) -> None:
    """药材不存在：返回 404（而非 500 或空数据）。"""
    client = _client(repo)  # 假图谱队列空 → 查询返回空
    assert client.get("/api/herb/不存在").status_code == 404  # 明确的 404 语义


def test_formula_endpoint_mask(repo: FakeRepo) -> None:
    """方剂端点：caution 剂量消毒。"""
    repo.enqueue(  # 预设 caution 方剂（含一个有毒组成）
        [
            {
                "name": "四逆汤",
                "usage": "水煎服",
                "safety_level": "caution",
                "components": [
                    {"herb": "附子", "dose": 9.0, "unit": "g", "role": "君", "toxicity": "有毒"}
                ],
                "patterns": [],
                "diseases": [],
            }
        ]
    )
    client = _client(repo)  # 组装客户端
    resp = client.get("/api/formula/四逆汤")  # GET 方剂详情端点
    body = resp.json()  # 响应负载
    assert body["usage"].startswith("【剂量信息已隐藏")  # 用法字段被掩码
    assert "9.0" not in str(body.get("_components", []))  # 毒性组成剂量数值不出现


def test_graph_endpoint(repo: FakeRepo) -> None:
    """图谱可视化数据：节点 + 边过滤。"""
    repo.enqueue([{"label": "Herb", "name": "黄芪"}])  # 第一次查询：节点列表
    repo.enqueue([{"source": "黄芪", "relation": "HAS_EFFECT", "target": "补气"}])  # 第二次查询：边列表
    client = _client(repo)  # 组装客户端
    resp = client.get("/api/graph")  # GET 图谱可视化端点
    body = resp.json()  # 响应负载
    assert body["nodes"] == [{"label": "Herb", "name": "黄芪"}]  # 节点原样返回
    # target 不在节点集 → 边被过滤
    assert body["edges"] == []  # "补气"节点不存在，悬挂边被剔除（前端不画断线）


def test_chat_llm_agent(repo: FakeRepo) -> None:
    """LLM Agent 注入：tool-loop 走通。"""
    import json  # 局部导入：仅本用例需要构造 JSON 决策

    repo.enqueue([{"name": "黄芪", "toxicity": "无毒", "dose_range": "9-30g", "effects": ["补气"]}])  # 预设无毒药材
    llm = FakeChatLLM(  # 脚本两步：先调工具再作答
        [
            json.dumps({"tool": "get_herb", "args": {"name": "黄芪"}}),  # 第一步：调 get_herb
            json.dumps({"answer": "黄芪补气，常用 9-30g。不构成医疗建议。"}),  # 第二步：作答（无毒药剂量可展示）
        ]
    )
    client = _client(repo, llm)  # 注入 LLM → tool-loop 模式
    resp = client.post("/api/chat", json={"question": "黄芪功效？"})  # POST 问答
    assert "补气" in resp.json()["answer"]  # 工具结果进入最终答案
