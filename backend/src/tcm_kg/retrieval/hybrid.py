# ===================================================================
# 文件：hybrid.py
# 用途：GraphRAG 混合检索——全文/向量/图扩展三路召回 + RRF 融合排序
# 所属层级：retrieval 层（C10 · 在线检索编排，被 agent 工具层依赖）
# 核心功能：
#   - Retriever 协议：检索器抽象（在线层只依赖协议，不依赖具体实现）
#   - reciprocal_rank_fusion：RRF 倒数排名融合（按 evidence_id 合并多路排序）
#   - HybridRetriever：三路召回编排器（lexical + vector + graph → RRF）
#   - rows_to_evidence：仓储行 → Evidence 统一证据对象（Neo4j adapter 复用）
# 设计要点：
#   融合用 RRF 而非分数加权——不同召回器（BM25/余弦/图跳数）分数尺度不一致，
#   RRF 只用排名（1/(k+rank)），天然免归一化；k=60 为业界默认平滑常数。
# ===================================================================

"""可替换的混合检索抽象。

在线层只依赖本模块的协议，因此 Neo4j 全文、Neo4j 向量索引或专用向量库
都可以独立演进。融合算法使用 RRF，避免不同召回器的分数尺度不一致。
"""

from __future__ import annotations  # 延迟注解求值，支持前向类型引用

from collections.abc import Callable, Iterable  # 可调用/可迭代抽象类型
from typing import Any, Protocol  # Any 任意类型；Protocol 结构化接口

from tcm_kg.api.contracts import Evidence  # 统一证据契约对象（在线层核心 DTO）


# 检索器协议：任何实现 search(query, limit) → [Evidence] 的对象皆可接入
class Retriever(Protocol):
    """检索器协议：实现方只需提供 search()，即可被 HybridRetriever 编排。"""

    def search(self, query: str, *, limit: int = 10) -> list[Evidence]: ...  # 检索入口


def reciprocal_rank_fusion(
    rankings: Iterable[Iterable[Evidence]], *, k: int = 60, limit: int = 20
) -> list[Evidence]:
    """按 evidence_id 融合多个排序结果，并保留最高分和来源元数据。

    RRF 公式：score(d) = Σ 1/(k + rank_i(d))，其中 rank_i 为文档 d 在第 i 路
    排序中的名次（从 1 起）。k 越大对头部名次的区分度越平缓（默认 60）。
    """
    # evidence_id → (累计 RRF 分, Evidence 对象)：同 ID 跨路累加分数
    fused: dict[str, tuple[float, Evidence]] = {}  # 累加器：ID → (累计分, 证据)
    for ranking in rankings:  # 逐路排序遍历
        for rank, evidence in enumerate(ranking, start=1):  # 名次从 1 起编号
            # 已有则取累计分累加，未有则从 0 起加（保留首次出现的 Evidence 元数据）
            current, _ = fused.get(evidence.evidence_id, (0.0, evidence))
            fused[evidence.evidence_id] = (current + 1.0 / (k + rank), evidence)  # 1/(k+rank) 累加
    # 按累计分降序，截取前 limit 条
    ordered = sorted(fused.values(), key=lambda item: item[0], reverse=True)
    result: list[Evidence] = []  # 融合结果容器
    for score, evidence in ordered[: max(1, limit)]:  # limit 钳到 ≥1，防空截断
        # 将融合分写回 Evidence.score（model_copy 避免改动原对象）
        result.append(evidence.model_copy(update={"score": score}))
    return result  # 返回融合排序后的证据列表


class HybridRetriever:
    """全文、向量和图扩展三路召回的 RRF 编排器。"""

    # 三路召回以可调用对象注入（函数/闭包均可），便于独立替换与测试打桩
    def __init__(
        self,
        lexical: Callable[[str, int], list[Evidence]],  # 词法召回（全文检索）
        vector: Callable[[str, int], list[Evidence]],  # 向量召回（语义相似）
        graph: Callable[[str, int], list[Evidence]],  # 图召回（邻居扩展）
    ) -> None:
        self._lexical = lexical  # 保存词法召回 callable
        self._vector = vector  # 保存向量召回 callable
        self._graph = graph  # 保存图召回 callable

    def search(self, query: str, *, limit: int = 10) -> list[Evidence]:
        # limit 夹紧到 [1, 100]：防 0/负数导致空结果、防超大值拖垮召回
        safe_limit = max(1, min(limit, 100))
        # 三路各自独立召回（同 limit），再交给 RRF 融合排序
        rankings = (
            self._lexical(query, safe_limit),  # 路一：词法召回
            self._vector(query, safe_limit),  # 路二：向量召回
            self._graph(query, safe_limit),  # 路三：图召回
        )
        return reciprocal_rank_fusion(rankings, limit=safe_limit)  # RRF 融合排序


def rows_to_evidence(rows: Iterable[dict[str, Any]]) -> list[Evidence]:
    """将仓储行转换为统一证据对象，便于 Neo4j adapter 复用。

    行字段约定：label/name 必有，text/relation/source_id/locator 可选；
    其余字段整体进 metadata（如分数、属性片段），供下游审计。
    """
    result: list[Evidence] = []  # 结果收集器
    for row in rows:  # 逐行转换为证据对象
        label = str(row.get("label") or "Unknown")  # 标签缺失兜底为 Unknown
        name = str(row.get("name") or "")  # 名称缺失兜底为空串
        result.append(
            Evidence(
                # 证据 ID 按"实体:标签:名称"构成，保证跨路融合时同名同实体命中
                evidence_id=f"entity:{label}:{name}",
                entity_label=label,  # 实体标签
                entity_name=name,  # 实体名称
                # 无 text 的行退化为实体名本身（检索片段至少可读）
                text=str(row.get("text") or name),
                # 有 relation 字段的行视为关系证据，否则为实体证据
                evidence_type="relation" if row.get("relation") else "entity",
                source_id=row.get("source_id"),  # 溯源源 ID（可空）
                locator=row.get("locator"),  # 原文定位符（可空）
                # 除主字段外全部并入 metadata，避免信息丢失
                metadata={k: v for k, v in row.items() if k not in {"label", "name", "text"}},
            )
        )
    return result  # 返回证据列表
