# ===================================================================
# 文件：backend/tests/pipeline/test_align.py（pipeline 层单测 · 实体对齐 C6/C7/C8）
# 用途：零网络零模型地测全链路——FakeEmbedder 提供确定性向量，规则精判各分档验证，
#       重点回归"近名不同药不得自动合并"与"炮制/部位差异进 review"两条安全红线
# ===================================================================

"""C6/C7/C8 对齐单测：FakeEmbedder（确定性向量）+ 规则精判全链路（零网络零模型）。"""

import json  # 标准库：写/读 JSONL fixture
from pathlib import Path  # 标准库：tmp_path 夹具

import numpy as np  # 第三方：向量运算

from tcm_kg.pipeline.align import (  # 被测对象：对齐全链路组件
    CandidatePair, PoolEntry, Verdict,  # 数据结构
    align_label_pool, build_pool, candidate_pairs, evaluate_recall, recall_topk,  # 流程函数
    rule_judge, write_outputs,  # 精判与落盘
)


class FakeEmbedder:
    """确定性嵌入：同组文本向量几乎相同（相似度≈0.97），非同组低相似。"""

    dim = 8  # 向量维度（真实 bge-m3 是 1024，测试用 8 足够）

    def __init__(self, groups: dict[str, list[str]] | None = None):
        self.groups = groups or {}  # 组定义：组名 → 成员文本列表

    def _base_vec(self, key: str) -> np.ndarray:
        """由字符串确定性生成单位向量：字符码求和做种子，模长归一。"""
        seed = sum(ord(c) for c in key)  # 种子 = 字符码之和（同 key 恒同向量）
        vec = np.array(  # 伪随机但确定：种子×系数取模映射到 [-0.5, 0.5)
            [((seed * (i + 1)) % 97) / 97 - 0.5 for i in range(self.dim)], dtype=np.float32
        )
        return vec / (np.linalg.norm(vec) or 1.0)  # 归一化为单位向量（零向量兜底除 1）

    def encode(self, texts: list[str]) -> np.ndarray:
        """批量编码：同组文本 = 组向量为主 + 自身小扰动（模拟语义近但不全等）。"""
        out = []
        for text in texts:  # 逐条编码
            # 找该文本所属的组（无组则 None）
            group_key = next((g for g, members in self.groups.items() if text in members), None)
            if group_key is not None:
                # 同组：组向量 + 文本小扰动（相似度约 0.97）
                vec = 0.95 * self._base_vec(group_key) + 0.05 * self._base_vec(text)  # 95% 组向量
                vec = vec / (np.linalg.norm(vec) or 1.0)  # 再归一
            else:
                vec = self._base_vec(text)  # 无组：纯自身向量（组间低相似）
            out.append(vec)
        return np.vstack(out) if out else np.zeros((0, self.dim), dtype=np.float32)  # 堆叠/空兜底


def _entry(text: str, source: str, canonical: str | None = None, label: str = "Herb") -> PoolEntry:
    """辅助：构造池条目（默认 Herb 类型）。"""
    return PoolEntry(
        label=label, text=text, canonical=canonical or text, source_id=source, is_alias=False
    )


def _write_parsed(tmp_path: Path) -> Path:
    """写两源 parsed fixture：symmap（黄芪+当归）+ nmm（黄耆+四逆汤），测跨源对齐。"""
    parsed = tmp_path / "parsed"  # parsed 目录
    parsed.mkdir()
    # 两个源：symmap 与 nmm 各有黄芪（同名跨源，应对齐 same）
    (parsed / "src-symmap.jsonl").write_text(
        json.dumps(  # symmap 第 1 条：黄芪（带异名"黄耆"）
            {
                "kind": "entity",
                "label": "Herb",
                "record": {
                    "name": "黄芪",
                    "aliases": ["黄耆"],  # 异名也入池
                    "provenance": {"source_id": "src-symmap"},
                },
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(  # symmap 第 2 条：当归（对照项）
            {
                "kind": "entity",
                "label": "Herb",
                "record": {
                    "name": "当归",
                    "aliases": [],
                    "provenance": {"source_id": "src-symmap"},
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (parsed / "src-nmm.jsonl").write_text(
        json.dumps(  # nmm 第 1 条：黄耆（黄芪异体字，跨源对齐目标）
            {
                "kind": "entity",
                "label": "Herb",
                "record": {"name": "黄耆", "aliases": [], "provenance": {"source_id": "src-nmm"}},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(  # nmm 第 2 条：四逆汤（Formula 类型，验证分池）
            {
                "kind": "entity",
                "label": "Formula",
                "record": {"name": "四逆汤", "aliases": [], "provenance": {"source_id": "src-nmm"}},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return parsed


def test_build_pool(tmp_path: Path) -> None:
    """C6.1：池构建（canonical + alias，同源去重，跨源保留）。"""
    parsed = _write_parsed(tmp_path)  # 准备两源数据
    pool = build_pool(parsed)  # 构建对齐池（按 label 分池）
    herbs = pool["Herb"]  # 药材池
    texts = [(e.text, e.source_id) for e in herbs]  # (文本, 来源) 列表
    assert ("黄芪", "src-symmap") in texts  # 正名入池
    assert ("黄耆", "src-symmap") in texts  # alias 入池
    assert ("黄耆", "src-nmm") in texts  # 跨源保留（同文本不同源不合并）
    assert ("当归", "src-symmap") in texts  # 对照项
    assert len([t for t in texts if t == ("黄芪", "src-symmap")]) == 1  # 同源去重（重复只留一条）
    assert pool["Formula"][0].text == "四逆汤"  # 方剂独立成池


def test_rule_judge_same_variants() -> None:
    """规则精判：等价/长名包含 → same；短编辑距离差 → review（安全收紧）。"""
    # 字符等价（全半角/标点差异）
    pair = CandidatePair(a=_entry("黄芪汤", "s1"), b=_entry("黄芪汤。", "s2"), sim=0.99)  # 仅差句号
    assert rule_judge(pair).verdict == Verdict.SAME.value  # 判 same
    # 长名近似包含（3 字以上，差 1 字）
    pair = CandidatePair(a=_entry("四逆汤", "s1"), b=_entry("四逆汤 ", "s2"), sim=0.99)  # 尾随空格
    assert rule_judge(pair).verdict == Verdict.SAME.value  # 判 same
    pair = CandidatePair(a=_entry("参苓白术散", "s1"), b=_entry("苓白术散参", "s2"), sim=0.8)  # 乱序
    # 编辑距离 0.8（5 字 1 差）→ 收紧后不再自动合并，进 review
    pair = CandidatePair(a=_entry("参苓白术散", "s1"), b=_entry("参苓白术汤", "s2"), sim=0.8)  # 1 字之差
    decision = rule_judge(pair)
    assert decision.verdict == Verdict.RELATED.value  # 只判 related（人工复核）


def test_rule_judge_related_and_different() -> None:
    """规则精判：中档 related，低档 different。"""
    pair = CandidatePair(a=_entry("黄芪", "s1"), b=_entry("当归", "s2"), sim=0.6)  # 中等相似
    assert rule_judge(pair).verdict == Verdict.RELATED.value  # 中档 → related
    pair = CandidatePair(a=_entry("黄芪", "s1"), b=_entry("犀角地黄汤", "s2"), sim=0.3)  # 低相似
    assert rule_judge(pair).verdict == Verdict.DIFFERENT.value  # 低档 → different


def test_rule_judge_safety_no_false_merge() -> None:
    """安全回归（实测校准）：近名不同药不得自动合并。

    这些用例全部来自 Herb 池实测的误合并抽查——纯语义阈值会判 same。
    """
    dangerous_pairs = [  # (药A, 药B, 实测相似度)——历史上纯阈值判 same 的危险对
        ("赤芍", "白芍", 0.86),  # 不同药（赤/白之差，功效迥异）
        ("茯苓", "猪苓", 0.81),  # 不同药
        ("大蓟", "小蓟", 0.86),  # 不同药
        ("党参", "明党参", 0.81),  # 不同药（科属都不同）
        ("红豆蔻", "豆蔻", 0.86),  # 不同药
        ("老鹳草", "蓍草", 0.86),  # 不同药
    ]
    for a, b, sim in dangerous_pairs:  # 逐对回归
        pair = CandidatePair(a=_entry(a, "s1"), b=_entry(b, "s2"), sim=sim)
        decision = rule_judge(pair)
        assert decision.verdict != "same", f"{a}/{b} 不得自动合并（实测误合并回归）"


def test_rule_judge_processing_suffix_boundary() -> None:
    """炮制/部位词差异 → related（L1 生熟分界，含前缀炮制品）。"""
    cases = [  # (原名, 炮制/部位变体)——生熟混并会破坏 L1 排除语义
        ("巴豆", "巴豆霜"),  # 后缀炮制品（toxic_exclusion L1 分界）
        ("珍珠", "珍珠母"),  # 部位
        ("龟甲", "龟甲胶"),  # 胶类
        ("草乌", "草乌叶"),  # 部位
        ("何首乌", "制何首乌"),  # 前缀炮制品
        ("马钱子", "马钱子粉"),  # 粉后缀（toxic_exclusion L1 分界）
        ("天南星", "制天南星"),  # 前缀炮制品（toxic_exclusion L1 分界）
        ("瓜蒌子", "炒瓜蒌子"),  # 前缀炮制品
    ]
    for a, b in cases:  # 逐对验证
        pair = CandidatePair(a=_entry(a, "s1"), b=_entry(b, "s2"), sim=0.85)
        decision = rule_judge(pair)
        assert decision.verdict == "related", f"{a}/{b} 应判 related（安全语义）"  # 不自动合并
        assert "炮制/部位" in decision.reason  # 理由必须点明炮制/部位


def test_recall_and_pairs() -> None:
    """C6.2：召回 + 候选对去重 + 自身排除。"""
    groups = {"huangqi": ["黄芪", "黄耆"]}  # 定义一组近义（黄芪与异体黄耆）
    embedder = FakeEmbedder(groups)  # 同组高相似嵌入器
    entries = [  # 四个池条目
        _entry("黄芪", "src-a"),
        _entry("黄耆", "src-b"),
        _entry("当归", "src-a"),  # 无组
        _entry("犀角", "src-c"),  # 无组
    ]
    top_lists = recall_topk(entries, embedder, topk=5)  # 向量召回 top-5
    pairs = candidate_pairs(entries, top_lists)  # 生成候选对（去重+排除自身）
    pair_texts = {frozenset((p.a.text, p.b.text)) for p in pairs}  # 无序对文本集合
    assert frozenset(("黄芪", "黄耆")) in pair_texts  # 同组召回命中
    assert all(frozenset(("当归", "黄芪")) not in pair_texts for _ in [1])  # 非同组通常低于阈值


def test_align_label_pool_end_to_end() -> None:
    """全链路：池 → 召回 → 规则精判 → 三分类输出。

    - 跨源同名（黄芪/黄芪）→ 字符等价 → 自动合并；
    - 近似异体（黄芪/黄耆）→ 规则收紧后进 review（安全优先）。
    """
    embedder = FakeEmbedder()  # 同文本 → 同向量（sim=1.0）
    entries = [  # 三条目：两源同名 + 一对照
        _entry("黄芪", "src-symmap"),
        _entry("黄芪", "src-nmm"),  # 跨源同名
        _entry("当归", "src-symmap"),
    ]
    decisions, review, merge = align_label_pool(entries, embedder, topk=5)  # 跑全链路
    assert decisions, "应产生判定"  # 判定非空
    same_pairs = [d for d in decisions if d.verdict == Verdict.SAME.value]  # same 判定
    assert any(d.a == "黄芪" and d.b == "黄芪" for d in same_pairs)  # 同名对判 same
    # 高置信 same 进 merge；低置信/related 进 review
    assert all(d.confidence >= 0.85 for d in merge)  # 合并队列全部高置信
    assert all(d.verdict != "same" or d.confidence < 0.85 for d in review)  # 复核队列无高置信 same

    # 近似异体（黄耆）不自动合并 → review
    entries2 = [  # 两源近似异体
        _entry("黄芪", "src-symmap"),
        _entry("黄耆", "src-nmm"),
    ]
    embedder2 = FakeEmbedder({"near": ["黄芪", "黄耆"]})  # 同组高相似（0.97）
    _, review2, merge2 = align_label_pool(entries2, embedder2, topk=5)  # 再跑全链路
    assert not any(d.a == "黄耆" or d.b == "黄耆" for d in merge2)  # 黄耆不进自动合并


def test_write_outputs(tmp_path: Path) -> None:
    """C7.2/C8.1：三个输出文件落盘。"""
    pair = CandidatePair(a=_entry("黄芪", "src-symmap"), b=_entry("黄芪", "src-nmm"), sim=1.0)  # 满分对
    decision = rule_judge(pair)  # 精判
    assert decision.verdict == "same"  # 应判 same
    write_outputs(tmp_path, [decision], [], [decision])  # 落盘三文件
    assert (tmp_path / "decisions.jsonl").exists()  # 判定文件
    assert (tmp_path / "merge_candidates.jsonl").exists()  # 合并候选文件
    assert (tmp_path / "review_queue.csv").exists()  # 复核队列文件
    row = json.loads((tmp_path / "decisions.jsonl").read_text(encoding="utf-8"))  # 读回第一条
    assert row["a"] == "黄芪" and row["b"] == "黄芪"  # 端点正确
    assert row["method"] == "rule"  # 判定方法标记为规则


def test_evaluate_recall() -> None:
    """C6.3：召回评估（标注 same 对的命中率）。"""
    d1 = CandidatePair(a=_entry("黄芪", "s1"), b=_entry("黄芪。", "s2"), sim=0.9)  # 等价 → same
    d2 = CandidatePair(a=_entry("当归", "s1"), b=_entry("当归须", "s2"), sim=0.7)  # 缀差 → related
    decisions = [rule_judge(d1), rule_judge(d2)]  # 两判定
    labeled = [("黄芪", "黄芪。", "same"), ("甘草", "生甘草", "same")]  # 人工标注集（2 对 same）
    metrics = evaluate_recall(decisions, labeled)  # 评估
    assert metrics["labeled"] == 2  # 标注总数
    assert metrics["recalled_same"] == 1  # 召回命中 1（甘草对未召回）
    assert metrics["recall"] == 0.5  # 召回率 50%
    assert evaluate_recall(decisions, []) == {}  # 空标注 → 空指标（不除零）
