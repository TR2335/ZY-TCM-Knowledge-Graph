# ===================================================================
# 文件：backend/tests/agent/test_graph.py（agent 层单测 · Agent 循环 E3）
# 用途：验证两类 Agent 的完整问答循环——
#   ① ToolLoopAgent：LLM 驱动的"调工具→拿结果→作答"多步循环
#      （含 E7 兜底警示、未知工具错误回传、步数上限、纯文本降级）
#   ② RuleBasedAgent：无 LLM 的规则路由降级（药材/方剂/辨证/禁忌四模板）
# 关键设计：FakeRepo 内存假图谱 + FakeChatLLM 脚本化回复，零网络零外部依赖
# ===================================================================

"""E3 Agent 循环单测：tool-loop 决策解析 + E7 兜底 + 规则路由（FakeChatLLM）。"""

import json  # 标准库：构造/解析 LLM 的 JSON 决策消息
from typing import Any  # 类型标注

import pytest  # 测试框架：fixture + asyncio 标记

from tcm_kg.agent.graph import RuleBasedAgent, ToolLoopAgent  # 被测对象：两种 Agent
from tcm_kg.agent.sanitizer import MASK, WARNING_TEMPLATE  # 掩码常量 + 标准警示模板
from tcm_kg.agent.tools import AgentTools  # 六工具集（被 Agent 调用）


class FakeRepo:
    """内存假图谱（脚本化查询结果队列）。

    测试前先 enqueue 若干"批次结果"，Agent 每发一条 Cypher 就按序弹出一批——
    这样可以精确控制"工具第一次查到什么、第二次查到什么"。
    """

    def __init__(self) -> None:
        self.queries: list[str] = []  # 记录收到的每条 Cypher（断言"确实查了图谱"用）
        self._queue: list[list[dict[str, Any]]] = []  # 预设结果队列（先进先出）

    def enqueue(self, rows: list[dict[str, Any]]) -> None:
        """压入一批预设查询结果（模拟一次 run 的返回）。"""
        self._queue.append(rows)  # 追加到队列尾

    def run(self, query: str, **_params: Any) -> list[dict[str, Any]]:
        """模拟 Neo4j 仓库的 run：记录查询并按序返回预设结果。"""
        self.queries.append(query)  # 留痕：测试断言可检查查询内容
        if self._queue:  # 队列还有预设
            return self._queue.pop(0)  # 弹出最早压入的一批
        return []  # 队列空 → 返回空结果（模拟"查无此数据"）


class FakeChatLLM:
    """脚本化对话 LLM：按序返回预设回复。

    真实 LLM 不可控且要花钱，测试时用预设脚本模拟它的每一次输出——
    比如第一次让它"决定调工具"，第二次让它"给出最终答案"。
    """

    def __init__(self, replies: list[str]):
        self._replies = list(replies)  # 预设回复列表（按序消费）

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """模拟对话接口：弹出下一条预设回复；耗尽后返回兜底答案。"""
        if self._replies:  # 还有预设
            return self._replies.pop(0)  # 按调用顺序返回
        return json.dumps({"answer": "（无更多回复）"})  # 耗尽兜底：合法 JSON 格式的答案


@pytest.fixture
def repo() -> FakeRepo:
    """每个测试拿到独立的假图谱（互不污染）。"""
    return FakeRepo()


# ---------- ToolLoopAgent（LLM 驱动的多步工具循环） ----------


@pytest.mark.asyncio  # 声明异步测试（pytest-asyncio 提供）
async def test_tool_loop_calls_tool_then_answers(repo: FakeRepo) -> None:
    """决策链：先调 get_herb 工具，再基于结果作答（含警示透传）。"""
    repo.enqueue(  # 预设工具查询结果：一条有毒药材附子的记录
        [{"name": "附子", "toxicity": "有毒", "dose_range": "3-15g", "effects": ["回阳救逆"]}]
    )
    llm = FakeChatLLM(  # 脚本化 LLM 的两步决策
        [
            json.dumps({"tool": "get_herb", "args": {"name": "附子"}}),  # 第一步：要求调 get_herb 工具
            json.dumps(  # 第二步：拿到工具结果后作答（把警示文案一并写入答案）
                {
                    "answer": "附子可回阳救逆。警示："
                    + WARNING_TEMPLATE.format(kind="药材", toxic_list="附子")
                }
            ),
        ]
    )
    agent = ToolLoopAgent(llm, AgentTools(repo))  # 组装被测 Agent
    answer = await agent.answer("附子有什么功效？")  # 触发完整问答循环
    assert "回阳救逆" in answer  # 答案含工具查到的功效
    assert "执业中医师" in answer  # 答案含就医引导警示
    assert repo.queries  # 确实调用了图谱（queries 非空说明工具执行过）


@pytest.mark.asyncio
async def test_tool_loop_e7_guard_appends_warning(repo: FakeRepo) -> None:
    """E7 兜底：答案含 MASK 但无警示文案 → 追加标准警示。

    场景：LLM 作答时把掩码内容直接输出，却忘了写警示——
    Agent 循环出口的 E7 防线必须补上，不能让掩码"裸奔"。
    """
    llm = FakeChatLLM([json.dumps({"answer": f"剂量信息：{MASK}"})])  # 一步直接作答（含掩码无警示）
    agent = ToolLoopAgent(llm, AgentTools(repo))  # 组装被测 Agent
    answer = await agent.answer("四逆汤剂量？")  # 触发问答
    assert "执业中医师" in answer  # 兜底补上了警示（E7 防线生效）


@pytest.mark.asyncio
async def test_tool_loop_unknown_tool_error_feedback(repo: FakeRepo) -> None:
    """未知工具：错误回传 LLM → LLM 改口作答。

    场景：LLM 幻觉出一个不存在的工具名——正确处理不是崩溃，
    而是把"工具不存在"作为错误反馈喂回 LLM，让它自己纠正。
    """
    llm = FakeChatLLM(
        [
            json.dumps({"tool": "不存在的工具", "args": {}}),  # 第一步：幻觉工具调用
            json.dumps({"answer": "抱歉，我直接回答：该问题需查询图谱。"}),  # 第二步：收到错误反馈后改口作答
        ]
    )
    agent = ToolLoopAgent(llm, AgentTools(repo))  # 组装被测 Agent
    answer = await agent.answer("测试")  # 触发问答
    assert "直接回答" in answer  # 循环正确走到第二阶段的文本答案


@pytest.mark.asyncio
async def test_tool_loop_max_steps(repo: FakeRepo) -> None:
    """步数上限：连续工具调用达上限 → 终止提示。

    场景：LLM 陷入"永远调工具、永不作答"的死循环——
    max_steps 上限保证循环必然终止并给出明确提示。
    """
    llm = FakeChatLLM([json.dumps({"tool": "search_entities", "args": {"name": "x"}})] * 20)  # 20 次全是工具调用
    agent = ToolLoopAgent(llm, AgentTools(repo), max_steps=3)  # 上限设为 3 步
    answer = await agent.answer("循环测试")  # 触发问答
    assert "最大工具调用步数" in answer  # 到达上限后的终止提示文案


@pytest.mark.asyncio
async def test_tool_loop_plain_text_answer(repo: FakeRepo) -> None:
    """非 JSON 回复：直接按文本答案处理。

    场景：LLM 没按约定的 JSON 格式输出，直接说了一句话——
    循环应把它当作最终答案原样返回，而不是解析报错。
    """
    llm = FakeChatLLM(["黄芪是补气药。"])  # 纯文本回复（非 JSON）
    agent = ToolLoopAgent(llm, AgentTools(repo))  # 组装被测 Agent
    answer = await agent.answer("黄芪是什么？")  # 触发问答
    assert "黄芪" in answer  # 文本答案被原样透传


# ---------- RuleBasedAgent（无 LLM 降级） ----------


@pytest.mark.asyncio
async def test_rule_agent_herb_detail(repo: FakeRepo) -> None:
    """规则路由：引号药材名 → 详情模板（含消毒）。"""
    repo.enqueue(  # 预设药材详情查询结果（字段最全）
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
    agent = RuleBasedAgent(AgentTools(repo))  # 规则 Agent（无需 LLM）
    answer = await agent.answer("查询「附子」的详情")  # 问题含「引号药材名」→ 命中详情路由
    assert "【附子】" in answer  # 详情模板的标题格式
    assert "回阳救逆" in answer  # 功效内容
    assert "执业中医师" in answer  # 有毒药材自动附警示


@pytest.mark.asyncio
async def test_rule_agent_formula_detail(repo: FakeRepo) -> None:
    """规则路由：方剂名（含'汤'）→ 方剂模板（剂量消毒）。"""
    repo.enqueue(  # 预设方剂详情查询结果：caution 方 + 有毒组成 + 无毒组成
        [
            {
                "name": "四逆汤",
                "source_book": "伤寒论",
                "efficacy": "回阳救逆",
                "usage": "水煎服",
                "safety_level": "caution",
                "components": [
                    {"herb": "附子", "dose": 9.0, "unit": "g", "role": "君", "toxicity": "有毒"},  # 毒药组成
                    {"herb": "干姜", "dose": 6.0, "unit": "g", "role": "臣", "toxicity": None},  # 无毒组成
                ],
                "patterns": ["少阴病证"],
                "diseases": [],
            }
        ]
    )
    agent = RuleBasedAgent(AgentTools(repo))  # 规则 Agent
    answer = await agent.answer("「四逆汤」的组成")  # 问题含「四逆汤」→ 命中方剂路由
    assert "【四逆汤】" in answer  # 方剂模板标题
    assert "附子（君）" in answer  # 组成行格式：药名（角色）
    assert "9.0g" not in answer  # 毒性组成剂量已消毒（数值不出现）
    assert "6.0g" in answer  # 无毒组成正常展示剂量
    assert "执业中医师" in answer  # caution 方剂附警示


@pytest.mark.asyncio
async def test_rule_agent_pattern_matching(repo: FakeRepo) -> None:
    """规则路由：辨证关键词 + 症状 → match_patterns 模板。"""
    repo.enqueue(  # 预设辨证匹配结果：一个候选证候 + 主/次症命中
        [{"pattern": "心脾两虚", "score": 5, "primary_hits": ["心悸"], "secondary_hits": ["失眠"]}]
    )
    agent = RuleBasedAgent(AgentTools(repo))  # 规则 Agent
    answer = await agent.answer("心悸失眠辨证是什么证")  # 含"辨证"关键词 + 症状 → 命中辨证路由
    assert "心脾两虚" in answer  # 输出候选证候名
    assert "主症命中：心悸" in answer  # 输出主症命中明细


@pytest.mark.asyncio
async def test_rule_agent_contraindication(repo: FakeRepo) -> None:
    """规则路由：配伍禁忌关键词 + 引号药材 → 禁忌检查模板。"""
    repo.enqueue(  # 第一次查询：禁忌关系存在（十八反药对）
        [{"a": "甘草", "b": "甘遂", "contra_type": "十八反", "verse": "藻戟遂芫俱战草"}]
    )
    repo.enqueue([])  # 第二次查询（反向查）：空
    repo.enqueue([])  # 第三次查询（补充信息）：空
    agent = RuleBasedAgent(AgentTools(repo))  # 规则 Agent
    answer = await agent.answer("「甘草」和「甘遂」能一起用吗（配伍禁忌）")  # 含"配伍禁忌"关键词 + 两个引号药材名
    assert "十八反" in answer  # 禁忌类型呈现
    assert "甘草 与 甘遂" in answer  # 药对呈现
    assert "执业中医师" in answer  # 禁忌场景强制就医引导
