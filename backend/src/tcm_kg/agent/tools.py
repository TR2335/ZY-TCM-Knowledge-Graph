# ===================================================================
# 文件：tools.py
# 用途：Agent 六工具集（检索/药材/方剂/辨证/配伍禁忌/只读Cypher）
# 所属层级：agent 层（E1 最小工具集 · Agent 与图谱交互的唯一接口）
# 核心功能：
#   1. search_entities 实体检索（名称/别名）
#   2. get_herb 药材详情（性味归经毒性，经 sanitize_herb 消毒）
#   3. get_formula 方剂详情 + 组成（经 sanitize_formula 含剂量消毒）
#   4. match_patterns 辨证（主症必配 + 次症加权）
#   5. check_contraindications 配伍禁忌（十八反/十九畏/妊娠）
#   6. run_cypher 只读 Cypher（白名单 + 危险词扫描 + 兜底消毒）
#   所有查询结果经 sanitizer 消毒后才输出
# 如何使用：
#   构造 AgentTools(repo)，由 Agent 循环按名调度；repo 可注入 FakeRepo 用于测试。
# ===================================================================

"""E1 Agent 工具集（六工具）：图谱查询 → 消毒后结构化输出。

工具（MASTER_PLAN E1 最小集）：
- search_entities(name)：全文/精确检索（name + aliases）；
- get_herb(name)：药材详情（性味归经毒性，经 sanitize_herb）；
- get_formula(name)：方剂详情 + 组成（经 sanitize_formula，含剂量消毒）；
- match_patterns(symptoms)：辨证（ADR-012：主症必配 + 次症加权 Cypher）；
- check_contraindications(herbs)：配伍禁忌（十八反/十九畏/妊娠）；
- run_cypher(query)：text2cypher 只读执行（E1.7 兜底消毒 + 白名单校验）。

工具返回 dict（供 LLM 与 API 复用）；repo 注入（测试用 FakeRepo）。
"""

from typing import Any, Protocol

from tcm_kg.agent.sanitizer import sanitize_cypher_rows, sanitize_formula, sanitize_herb
from tcm_kg.api.contracts import Evidence
from tcm_kg.core.logging import get_logger

logger = get_logger(__name__)

# run_cypher 白名单（只读首子句）与全串危险关键词（E1.7 旁路防护）
_READONLY_ALLOWED = (
    "MATCH",
    "RETURN",
    "WITH",
    "UNWIND",
    "OPTIONAL",
)
_FORBIDDEN_KEYWORDS = (
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET ",
    "REMOVE",
    "DROP",
    "LOAD CSV",
    "CALL",
    "FOREACH",
)


class GraphRepo(Protocol):
    """工具层依赖的最小 repo 协议（Neo4jRepo 实现；测试 FakeRepo）。"""

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]: ...


# 六工具实现：封装图谱查询并在出口做安全消毒（Agent 调用图谱的唯一入口）
class AgentTools:
    """六工具实现（图谱查询 + 安全消毒）。"""

    # 注入图仓储 repo；工具方法通过 self._repo.run 执行参数化 Cypher
    def __init__(self, repo: GraphRepo, *, allow_raw_cypher: bool = True):
        self._repo = repo
        self._allow_raw_cypher = allow_raw_cypher

    def ready(self) -> bool:
        """供 readiness 探针使用，FakeRepo 不实现时按可用处理。"""
        ping = getattr(self._repo, "ping", None)
        return bool(ping()) if callable(ping) else True

    def graph_snapshot(self, limit: int = 120) -> dict[str, list[dict[str, Any]]]:
        """领域化图谱快照；前端不再通过 Agent 执行任意 Cypher。"""
        safe_limit = max(1, min(int(limit), 2000))
        nodes = self._repo.run(
            "MATCH (n) RETURN labels(n)[0] AS label, n.name AS name, "
            "n.toxicity AS toxicity, n.safety_level AS safety_level LIMIT $limit",
            limit=safe_limit,
        )
        edges = self._repo.run(
            "MATCH (a)-[r]->(b) RETURN a.name AS source, type(r) AS relation, "
            "b.name AS target LIMIT $limit",
            limit=safe_limit * 2,
        )
        names = {n.get("name") for n in nodes}
        return {
            "nodes": nodes,
            "edges": [e for e in edges if e.get("source") in names and e.get("target") in names],
        }

    def entity_neighbors(self, label: str, name: str, limit: int = 50) -> list[dict[str, Any]]:
        """查询实体一跳邻居；标签只接受字母数字下划线，避免拼接注入。"""
        import re

        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", label):
            raise ValueError("非法实体标签")
        return self._repo.run(
            f"MATCH (n:{label} {{name: $name}})-[r]-(m) "
            "RETURN labels(m)[0] AS label, m.name AS name, type(r) AS relation "
            "LIMIT $limit",
            name=name,
            limit=max(1, min(int(limit), 200)),
        )

    def graph_stats(self) -> dict[str, Any]:
        """返回数据规模，供运营页和发布检查使用。"""
        labels = getattr(self._repo, "counts_by_label", None)
        relations = getattr(self._repo, "counts_by_rel", None)
        return {
            "labels": labels() if callable(labels) else {},
            "relations": relations() if callable(relations) else {},
        }

    # ---------- 工具 1：实体检索 ----------

    # 按名称/别名跨多标签检索实体，返回含毒性与安全级标记的精简列表
    def search_entities(self, name: str, limit: int = 10) -> list[dict[str, Any]]:
        """按名称/别名检索实体（含毒性与安全级标记）。"""
        rows = self._repo.run(
            """
            UNWIND ['Herb','Formula','Pattern','DiseaseTCM','SymptomTCM',
                    'TreatmentMethod','Effect'] AS label
            CALL apoc.cypher.run(
                'MATCH (n:' + label + ') WHERE n.name CONTAINS $name
                 OR ANY(a IN coalesce(n.aliases,[]) WHERE a CONTAINS $name)
                 RETURN label AS label, n.name AS name,
                        n.toxicity AS toxicity, n.safety_level AS safety_level LIMIT $limit',
                {name: $name, limit: $limit}
            ) YIELD value
            RETURN value.label AS label, value.name AS name,
                   value.toxicity AS toxicity, value.safety_level AS safety_level
            """,
            name=name,
            limit=limit,
        )
        return rows

    def search_evidence(self, query: str, limit: int = 10) -> list[Evidence]:
        """把结构化实体检索转换成 API/Agent 可引用的 Evidence。"""
        rows = self.search_entities(query, limit=limit)
        return [
            Evidence(
                evidence_id=f"entity:{row.get('label', 'Unknown')}:{row.get('name', '')}",
                evidence_type="entity",
                entity_label=row.get("label"),
                entity_name=row.get("name"),
                text=str(row.get("name") or ""),
                metadata={
                    "toxicity": row.get("toxicity"),
                    "safety_level": row.get("safety_level"),
                },
            )
            for row in rows
        ]

    # ---------- 工具 2：药材详情 ----------

    # 查药材详情（性味归经毒性功效），无结果返回 None，命中即经 sanitize_herb 消毒
    def get_herb(self, name: str) -> dict[str, Any] | None:
        """药材详情（性味归经毒性 + 功效），经安全消毒。"""
        rows = self._repo.run(
            """
            MATCH (h:Herb {name: $name})
            OPTIONAL MATCH (h)-[:HAS_EFFECT]->(e:Effect)
            RETURN h.name AS name, h.aliases AS aliases, h.pinyin AS pinyin,
                   h.nature AS nature, h.flavors AS flavors, h.meridians AS meridians,
                   h.toxicity AS toxicity, h.dose_range AS dose_range,
                   h.is_excluded_in_formula AS is_excluded_in_formula,
                   collect(DISTINCT e.name) AS effects
            """,
            name=name,
        )
        if not rows:
            return None
        record = dict(rows[0])
        record["effects"] = [e for e in record.get("effects", []) if e]
        return sanitize_herb(record)

    # ---------- 工具 3：方剂详情 ----------

    def get_formula(self, name: str) -> dict[str, Any] | None:
        """方剂详情 + 组成（剂量经消毒），含主治证候/疾病。"""
        rows = self._repo.run(
            """
            MATCH (f:Formula {name: $name})
            OPTIONAL MATCH (f)-[c:COMPOSED_OF]->(h:Herb)
            OPTIONAL MATCH (f)-[:TREATS]->(p:Pattern)
            OPTIONAL MATCH (f)-[:TREATS_DISEASE]->(d:DiseaseTCM)
            RETURN f.name AS name, f.efficacy AS efficacy, f.usage AS usage,
                   f.source_book AS source_book, f.safety_level AS safety_level,
                   collect(DISTINCT {herb: h.name, dose: c.dose, unit: c.unit,
                                     raw_dose: c.raw_dose, role: c.role,
                                     toxicity: h.toxicity}) AS components,
                   collect(DISTINCT p.name) AS patterns,
                   collect(DISTINCT d.name) AS diseases
            """,
            name=name,
        )
        if not rows:
            return None
        record = dict(rows[0])
        components = [c for c in record.get("components", []) if c.get("herb")]
        record["patterns"] = [p for p in record.get("patterns", []) if p]
        record["diseases"] = [d for d in record.get("diseases", []) if d]
        return sanitize_formula(record, components)

    # ---------- 工具 4：辨证（ADR-012） ----------

    def match_patterns(self, symptoms: list[str], topk: int = 5) -> list[dict[str, Any]]:
        """辨证：主症必配 + 次症加权评分（GB/T 16751.2 口径）。

        评分：命中药候主症数 ×2 + 次症数 ×1（归一化到 [0,1]）。
        """
        rows = self._repo.run(
            """
            UNWIND $symptoms AS s
            MATCH (p:Pattern)-[hs:HAS_SYMPTOM]->(st:SymptomTCM {name: s})
            WITH p,
                 sum(CASE WHEN hs.is_primary THEN 2 ELSE 1 END) AS score,
                 collect(CASE WHEN hs.is_primary THEN st.name END) AS primary_hits,
                 collect(CASE WHEN hs.is_primary THEN null ELSE st.name END) AS secondary_hits
            RETURN p.name AS pattern, score,
                   [x IN primary_hits WHERE x IS NOT NULL] AS primary_hits,
                   [x IN secondary_hits WHERE x IS NOT NULL] AS secondary_hits
            ORDER BY score DESC LIMIT $topk
            """,
            symptoms=symptoms,
            topk=topk,
        )
        results = []
        for row in rows:
            results.append(
                {
                    "pattern": row["pattern"],
                    "score": row["score"],
                    "primary_hits": [x for x in row.get("primary_hits", []) if x],
                    "secondary_hits": [x for x in row.get("secondary_hits", []) if x],
                }
            )
        return results

    # ---------- 工具 5：配伍禁忌 ----------

    def check_contraindications(self, herbs: list[str]) -> dict[str, Any]:
        """检查配伍禁忌：十八反/十九畏 + 妊娠禁忌 + 毒性提示。"""
        contra_rows = self._repo.run(
            """
            UNWIND $herbs AS a
            UNWIND $herbs AS b
            MATCH (x:Herb {name: a})-[c:CONTRAINDICATED_WITH]-(y:Herb {name: b})
            WHERE a < b
            RETURN a, b, c.type AS contra_type, c.verse AS verse
            """,
            herbs=herbs,
        )
        pregnancy_rows = self._repo.run(
            """
            UNWIND $herbs AS a
            MATCH (h:Herb {name: a})-[p:PREGNANCY_CONTRA]->()
            RETURN a, p.level AS level
            """,
            herbs=herbs,
        )
        toxicity_rows = self._repo.run(
            """
            UNWIND $herbs AS a
            MATCH (h:Herb {name: a})
            WHERE h.toxicity IN ['有毒', '大毒']
            RETURN a, h.toxicity AS toxicity
            """,
            herbs=herbs,
        )
        conflicts = [
            {
                "herb_a": r["a"],
                "herb_b": r["b"],
                "type": r.get("contra_type"),
                "verse": r.get("verse"),
            }
            for r in contra_rows
        ]
        pregnancy = [{"herb": r["a"], "level": r.get("level")} for r in pregnancy_rows]
        toxicity = [{"herb": r["a"], "toxicity": r.get("toxicity")} for r in toxicity_rows]
        return {
            "has_conflicts": bool(conflicts),
            "conflicts": conflicts,
            "pregnancy_warnings": pregnancy,
            "toxicity_warnings": toxicity,
        }

    # ---------- 工具 6：只读 Cypher（text2cypher 兜底） ----------

    # 执行只读 Cypher（text2cypher 兜底）：首子句白名单 + 全串危险词扫描 + E1.7 兜底消毒
    def run_cypher(self, query: str, limit_rows: int = 50) -> list[dict[str, Any]]:
        """执行只读 Cypher（白名单 + 危险词全串扫描 + E1.7 兜底消毒）。

        拒绝：首子句不在白名单，或全串含写操作关键词
        （MATCH 开头仍可能携带 DETACH DELETE，必须全串扫描）。
        """
        if not self._allow_raw_cypher:
            raise PermissionError("生产 Agent 未开放原始 Cypher，请使用领域检索工具")
        normalized = " ".join(query.strip().split())
        first_word = normalized.split(" ", 1)[0].upper() if normalized else ""
        if first_word not in _READONLY_ALLOWED:
            raise ValueError(f"仅允许只读查询（白名单：{_READONLY_ALLOWED}），拒绝执行")
        upper = f" {normalized.upper()} "
        for keyword in _FORBIDDEN_KEYWORDS:
            if keyword in upper:
                raise ValueError(f"检测到写操作关键词 {keyword}，仅允许只读查询")
        rows = self._repo.run(query)
        return sanitize_cypher_rows(rows[:limit_rows])
