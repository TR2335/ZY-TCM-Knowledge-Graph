# ===================================================================
# 文件：graph.py
# 用途：Agent 循环实现，含 ToolLoopAgent（LLM 驱动多步工具调用）
#       与 RuleBasedAgent（规则路由降级模式），含 E7 安全兜底
# 所属层级：agent 层（E3 · LangGraph 语义的 tool-loop 薄封装）
# 核心功能：
#   - ToolLoopAgent：LLM 输出 JSON 决策调工具或直接作答，循环至产出答案或达步数上限
#   - RuleBasedAgent：无 LLM 时的离线降级，按问题关键词路由到对应工具并模板化回答
#   - E7 兜底：最终答案含 MASK/毒性但缺警示 → 追加标准就医警示文案
# 如何使用：
#   构造 ToolLoopAgent(llm, tools) 或 RuleBasedAgent(tools)，调用 await agent.answer(question)
#   返回字符串答案；LLM 依赖可注入，无配置时自动降级为规则 Agent。
# ===================================================================

"""E3 Agent 循环（LangGraph tool-loop）+ E7 安全兜底。

架构（ADR-002 薄封装）：
- agent 节点：LLM 决策（调工具或直接回答）；
- tools 节点：执行 AgentTools 六工具；
- 循环直到 LLM 产出最终答案或达步数上限；
- E7 兜底：最终答案若含毒性相关内容但缺警示 → 追加标准警示（LLM 不可配时
  降级为纯工具问答模式：规则路由 query → tool → 模板化回答）。

LLM 依赖可注入（StructuredLLM 协议）；无 LLM 配置时用 RuleBasedAgent
（离线演示/测试垂直切片全流程）。
"""

import json
from collections.abc import Callable
from typing import Any, Protocol

from tcm_kg.agent.sanitizer import MASK, WARNING_TEMPLATE
from tcm_kg.agent.tools import AgentTools
from tcm_kg.core.logging import get_logger

logger = get_logger(__name__)

MAX_STEPS = 8

SYSTEM_PROMPT = """你是中医药知识图谱助手"本草通"。使用工具查询图谱回答用户问题。

规则：
1. 优先调用工具获取事实，不要凭记忆编造中医药知识。
2. 工具结果中带 `_safety_warning` 字段时，必须在回答中原样包含该警示文案。
3. 涉及毒性药材/方剂剂量的问题：明确告知剂量信息出于安全考虑不予提供，引导就医。
4. 你提供的是知识查询服务，不构成医疗建议；回答末尾附一句就医提示。
5. 工具结果足够时立即作答，不要过度调用。"""

TOOL_SPECS = [
    {
        "name": "search_entities",
        "description": "按名称或别名检索实体（药材/方剂/证候/疾病等）",
        "parameters": {"name": "检索词", "limit": "返回数（默认10）"},
    },
    {
        "name": "get_herb",
        "description": "查询药材详情（性味归经毒性功效）",
        "parameters": {"name": "药材规范名"},
    },
    {
        "name": "get_formula",
        "description": "查询方剂详情（组成/主治/用法）",
        "parameters": {"name": "方剂规范名"},
    },
    {
        "name": "match_patterns",
        "description": "辨证：按症状列表匹配证候（主症加权）",
        "parameters": {"symptoms": "症状名列表"},
    },
    {
        "name": "check_contraindications",
        "description": "检查一组药材的配伍禁忌（十八反/十九畏/妊娠）",
        "parameters": {"herbs": "药材名列表"},
    },
    {
        "name": "run_cypher",
        "description": "执行只读 Cypher 查询（复杂检索兜底）",
        "parameters": {"query": "Cypher 语句（只读）"},
    },
]


class ChatLLM(Protocol):
    """对话 LLM 协议（Agent 循环用；测试注入 FakeChatLLM）。"""

    async def chat(self, messages: list[dict[str, str]]) -> str: ...


# LLM 驱动的 tool-loop Agent：LLM 输出 JSON 调工具或直接作答，循环至产出答案或达步数上限
class ToolLoopAgent:
    """E3 Agent（LangGraph 语义的 tool-loop 薄封装）。

    - LLM 输出 JSON {"tool": name, "args": {...}} 调工具；
    - LLM 输出 {"answer": text} 直接作答；
    - E7：答案含 MASK/毒字但无警示 → 追加标准警示。
    """

    def __init__(self, llm: ChatLLM, tools: AgentTools, max_steps: int = MAX_STEPS):
        self._llm = llm
        self._tools = tools
        self._max_steps = max_steps

    # 主循环：LLM 输出 JSON 决策（调工具/直接作答），工具结果回填消息，最终经 E7 兜底返回
    async def answer(self, question: str) -> str:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + "\n\n工具清单：\n"
                + json.dumps(TOOL_SPECS, ensure_ascii=False),
            },
            {"role": "user", "content": question},
        ]
        for _step in range(self._max_steps):
            raw = await self._llm.chat(messages)
            try:
                decision = json.loads(raw)
            except json.JSONDecodeError:
                return self._e7_guard(raw)
            if "answer" in decision:
                return self._e7_guard(str(decision["answer"]))
            tool_name = decision.get("tool")
            args = decision.get("args", {})
            tool_result = self._call_tool(tool_name, args)
            messages.append({"role": "assistant", "content": raw})
            result_text = json.dumps(tool_result, ensure_ascii=False, default=str)
            messages.append(
                {
                    "role": "user",
                    "content": f"工具结果（{tool_name}）：\n{result_text}",
                }
            )
        return self._e7_guard("已达最大工具调用步数。请提供更具体的问题。")

    def _call_tool(self, name: str, args: dict[str, Any]) -> Any:
        dispatch: dict[str, Callable[..., Any]] = {
            "search_entities": self._tools.search_entities,
            "get_herb": self._tools.get_herb,
            "get_formula": self._tools.get_formula,
            "match_patterns": self._tools.match_patterns,
            "check_contraindications": self._tools.check_contraindications,
            "run_cypher": self._tools.run_cypher,
        }
        handler = dispatch.get(name)
        if handler is None:
            return {"error": f"未知工具 {name}"}
        try:
            return handler(**args)
        except Exception as exc:  # noqa: BLE001 工具错误回传 LLM
            logger.warning("tool_error", tool=name, error=str(exc)[:200])
            return {"error": str(exc)[:300]}

    @staticmethod
    def _e7_guard(text: str) -> str:
        """E7 兜底：毒性相关答案缺警示 → 追加标准警示。"""
        if MASK in text:
            if "执业中医师" not in text:
                return text + "\n\n" + WARNING_TEMPLATE.format(kind="内容", toxic_list="毒性成分")
            return text
        return text


class RuleBasedAgent:
    """无 LLM 降级 Agent（垂直切片离线演示）。

    规则路由：问题含"证/辨证"→ match_patterns；含"禁忌/相反"→
    check_contraindications；含"方"→ get_formula；含"药/草"→ get_herb；
    否则 search_entities。回答为模板化结构（含警示透传）。
    """

    def __init__(self, tools: AgentTools):
        self._tools = tools

    async def answer(self, question: str) -> str:
        # 辨证路由：提取引号内症状列表或常见症状词
        if "辨证" in question or "什么证" in question:
            symptoms = self._extract_symptoms(question)
            if symptoms:
                results = self._tools.match_patterns(symptoms)
                if results:
                    lines = []
                    for r in results[:3]:
                        primary = "、".join(r["primary_hits"]) or "—"
                        secondary = "、".join(r["secondary_hits"]) or "—"
                        lines.append(f"- {r['pattern']}（评分 {r['score']}）")
                        lines.append(f"  主症命中：{primary}｜次症命中：{secondary}")
                    return (
                        "根据症状辨证（GB/T 16751.2 主症必配+次症加权）：\n"
                        + "\n".join(lines)
                        + "\n\n以上为知识查询结果，不构成医疗建议，请由执业中医师确认。"
                    )
                return "未匹配到证候，请确认症状名称或补充更多症状。"
        # 禁忌路由
        if (
            "禁忌" in question
            or "相反" in question
            or "配伍" in question
            or "一起用" in question
            or "同用" in question
            or "同服" in question
            or "合用" in question
        ):
            herbs = self._extract_names(question)
            if len(herbs) >= 2:
                result = self._tools.check_contraindications(herbs)
                return self._format_contraindication(result)
        # 实体详情路由：优先引号内名称
        names = self._extract_names(question)
        if names:
            if "方" in names[0] or "汤" in names[0] or "散" in names[0] or "丸" in names[0]:
                detail = self._tools.get_formula(names[0])
                if detail:
                    return self._format_formula(detail)
            detail = self._tools.get_herb(names[0])
            if detail:
                return self._format_herb(detail)
        # 检索兜底
        keyword = self._first_keyword(question)
        if keyword:
            rows = self._tools.search_entities(keyword)
            if rows:
                listing = "\n".join(f"- [{r.get('label')}] {r.get('name')}" for r in rows[:8])
                return f"检索结果：\n{listing}\n\n可继续询问具体实体详情。"
        return "未能理解问题。可尝试：查询某药材/方剂详情、辨证（列出症状）、检查配伍禁忌。"

    # ---------- 路由辅助 ----------

    _COMMON_SYMPTOMS = (
        "心悸",
        "失眠",
        "健忘",
        "乏力",
        "气短",
        "自汗",
        "头晕",
        "头痛",
        "发热",
        "恶寒",
        "咳嗽",
        "口渴",
        "便秘",
        "腹泻",
        "食少",
        "腹胀",
        "痛经",
        "月经不调",
        "面色苍白",
        "尿黄",
    )

    # 提取症状列表：优先取引号内名称，否则从常见症状词表中匹配命中项
    def _extract_symptoms(self, question: str) -> list[str]:
        quoted = self._extract_names(question)
        if quoted:
            return quoted
        return [s for s in self._COMMON_SYMPTOMS if s in question]

    _NAME_QUOTES = "\"'“”‘’「」『』《》〈〉"

    @classmethod
    def _extract_names(cls, question: str) -> list[str]:
        """提取各类引号/书名号内的名称。"""
        import re

        quotes = re.escape(cls._NAME_QUOTES)
        pattern = f"[{quotes}]([^{quotes}]{{1,20}})[{quotes}]"
        return re.findall(pattern, question)

    @staticmethod
    def _first_keyword(question: str) -> str | None:
        """问题中最长的中文词（≥2 字）作为检索词。"""
        import re

        words = re.findall(r"[\u4e00-\u9fff]{2,6}", question)
        stop = {"什么", "怎么", "如何", "哪些", "可以", " please", "请问", "查询", "搜索"}
        candidates = [w for w in words if w not in stop]
        return candidates[0] if candidates else None

    # ---------- 输出模板 ----------

    def _format_herb(self, d: dict[str, Any]) -> str:
        parts = [f"【{d['name']}】"]
        if d.get("nature") or d.get("flavors"):
            parts.append(
                f"性味：{d.get('nature', '—')}，味{'、'.join(d.get('flavors', []) or ['—'])}"
            )
        if d.get("meridians"):
            parts.append(f"归经：{'、'.join(d['meridians'])}")
        if d.get("toxicity"):
            parts.append(f"毒性：{d['toxicity']}")
        if d.get("dose_range"):
            parts.append(f"常用剂量：{d['dose_range']}")
        if d.get("effects"):
            parts.append(f"功效：{'、'.join(d['effects'])}")
        if d.get("_safety_warning"):
            parts.append("")
            parts.append(d["_safety_warning"])
        parts.append("")
        parts.append("以上为知识查询结果，不构成医疗建议，请由执业中医师确认。")
        return "\n".join(parts)

    def _format_formula(self, d: dict[str, Any]) -> str:
        parts = [f"【{d['name']}】"]
        if d.get("source_book"):
            parts.append(f"出处：{d['source_book']}")
        if d.get("efficacy"):
            parts.append(f"功用：{d['efficacy']}")
        components = d.get("_components") or []
        if components:
            comp_lines = []
            for c in components:
                role = f"（{c['role']}）" if c.get("role") else ""
                dose_text = (
                    f" {c['dose']}{'g' if c.get('unit') == 'g' else c.get('unit', '')}"
                    if c.get("dose")
                    else (f" {c['raw_dose']}" if c.get("raw_dose") else "")
                )
                comp_lines.append(f"  - {c['herb']}{role}{dose_text}")
            parts.append("组成：\n" + "\n".join(comp_lines))
        elif d.get("usage"):
            parts.append(f"用法：{d['usage']}")
        if d.get("patterns"):
            parts.append(f"主治证候：{'、'.join(d['patterns'])}")
        if d.get("_safety_warning"):
            parts.append("")
            parts.append(d["_safety_warning"])
        parts.append("")
        parts.append("以上为知识查询结果，不构成医疗建议，请由执业中医师确认。")
        return "\n".join(parts)

    # 配伍禁忌结果格式化：列出冲突/妊娠禁忌/毒性提示，无冲突则提示仍需医师确认
    def _format_contraindication(self, d: dict[str, Any]) -> str:
        parts = []
        if d.get("conflicts"):
            parts.append("⚠ 配伍禁忌（绝对不可同用）：")
            for c in d["conflicts"]:
                verse = f"（{c['verse']}）" if c.get("verse") else ""
                parts.append(f"  - {c['herb_a']} 与 {c['herb_b']} {c.get('type', '禁忌')}{verse}")
        if d.get("pregnancy_warnings"):
            parts.append("⚠ 妊娠禁忌：")
            for p in d["pregnancy_warnings"]:
                parts.append(f"  - {p['herb']}（{p.get('level', '慎用')}）")
        if d.get("toxicity_warnings"):
            parts.append("⚠ 毒性提示：")
            for t in d["toxicity_warnings"]:
                parts.append(f"  - {t['herb']}（{t.get('toxicity')}）")
        if not parts:
            return "未检出配伍禁忌（仍需执业中医师确认）。"
        parts.append("")
        parts.append("以上为安全提示，不构成医疗建议，请由执业中医师确认。")
        return "\n".join(parts)
