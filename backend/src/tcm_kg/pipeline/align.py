# ====================================================================
# 文件：align.py
# 用途：数据管线第三阶段（C6-C8）——实体对齐与合并，处理多源同名实体的冲突
# 所属层级：tcm_kg.pipeline 对齐层（离线管线 Phase C 第 3 步）
# 核心功能：
#   1. C6.1 候选池：parsed/*.jsonl 全部实体（canonical + aliases）入池，按 label 分池
#   2. C6.2 召回：bge-m3 嵌入 + 余弦相似度，每实体 top-K 相似候选（跨源/跨条目）
#   3. C7 精判：LLM（可选）判 same/related/different；无 LLM 走规则降级
#      （字符等价/炮制词差异/包含/编辑距离/相似度阈值组合，安全优先）
#   4. C8 审核队列：低置信与 related 对 → review_queue.csv
#      same 高置信对 → merge_candidates.jsonl（供 C9 加载器消费）
#   5. 输出 data/aligned/：decisions.jsonl / review_queue.csv / merge_candidates.jsonl
# 如何使用：
#   from tcm_kg.pipeline.align import align_label_pool, write_outputs
#   decisions, review, merge = align_label_pool(entries, embedder, judge=...)
#   write_outputs(out_dir, decisions, review, merge)
# ====================================================================
"""实体对齐（C6 召回 → C7 精判 → C8 审核队列）。

流程（MASTER_PLAN）：
- C6.1 候选池：parsed JSONL 全部实体（同 label 分池），canonical name + aliases 入池，
  记录 (label, name, source_id, is_alias_of)；
- C6.2 召回：候选池文本 bge-m3 嵌入（内存索引）→ 每个实体 top-K 相似候选
  （余弦；只保留跨源或跨条目对，自比对排除）；
- C6.3 召回评估：人工标注 50 对算召回率（labeled_pairs.csv，可选）；
- C7 精判：LLM（可选注入）判 same/related/different + 理由；
  无 LLM 时规则降级（字符等价/包含/编辑距离/相似度阈值组合）；
- C8 审核队列：低置信与 related 对 → review_queue.csv（人工裁决回流）；
  same 高置信对 → merge_candidates.jsonl（C9 加载器按规范名 MERGE 消费）。

输出（data/aligned/）：
- decisions.jsonl：全部判定对（a, b, label, verdict, confidence, reason, method, sim）
- review_queue.csv：待人工（C8.1 清单：实体对+相似度+建议+理由）
- merge_candidates.jsonl：自动合并候选（verdict=same 且 confidence >= auto_merge_threshold）
"""

import csv  # review_queue.csv 写入
import json  # JSONL 读写（候选池构建/判定落盘）
from dataclasses import dataclass  # 候选池/候选对/判定记录数据类
from enum import StrEnum  # 字符串枚举（Verdict 结论）
from pathlib import Path  # parsed/aligned 目录路径
from typing import Any  # LLM 判定器注入类型（避免硬依赖具体协议）

import numpy as np  # 嵌入矩阵运算（余弦相似度）

from tcm_kg.core.logging import get_logger  # 项目统一结构化日志
from tcm_kg.pipeline.embedding import Embedder  # 嵌入器协议（召回阶段用）

logger = get_logger(__name__)  # 模块级 logger

# 相似度阈值（bge-m3 实测标定：别名≈0.58，无关≈0.25）
SIM_HARD_SAME = 0.90  # 规则精判：几乎同形（含归一化等价）
SIM_AUTO_MERGE = 0.75  # same 判定的自动合并线（规则模式下）
SIM_CANDIDATE = 0.45  # 召回候选线：低于此不进精判
SIM_LIKELY_DIFFERENT = 0.35  # 明显不同（规则模式直接 different）


class Verdict(StrEnum):
    """精判结论。"""

    SAME = "same"  # 同一实体（可自动合并）
    RELATED = "related"  # 同大类不同实体（进人工审核，防误合并）
    DIFFERENT = "different"  # 无关实体


# 候选池条目：一个可对齐名称（规范名或别名）
@dataclass(frozen=True)
class PoolEntry:
    """候选池条目：一个可对齐名称（canonical 或 alias）。"""

    label: str  # 实体类型标签（如 Herb/Formula）
    text: str  # canonical name 或 alias 文本
    canonical: str  # 归属的规范名
    source_id: str  # 来源数据源标识（跨源对齐依据）
    is_alias: bool  # 是否别名条目（False = 规范名本身）


@dataclass
class CandidatePair:
    """召回出的一对候选（待精判）。"""

    a: PoolEntry  # 候选端 A
    b: PoolEntry  # 候选端 B
    sim: float  # 两端文本的余弦相似度


@dataclass
class Decision:
    """精判结论（decisions.jsonl 一行）。"""

    label: str  # 实体类型标签
    a: str  # 规范名 A
    b: str  # 规范名 B
    a_source: str  # A 的来源源标识
    b_source: str  # B 的来源源标识
    sim: float  # 语义相似度（召回阶段计算）
    verdict: str  # 判定结论：same/related/different
    confidence: float  # 判定置信度 [0,1]
    reason: str  # 判定理由（中文一句话）
    method: str  # rule | llm | human


# ---------- C6.1 候选池 ----------


# 构建候选池：parsed/*.jsonl 全部实体（canonical + aliases）入池，按 label 分池
# 同源同文本去重，跨源保留（正是要判定的对象）
def build_pool(parsed_dir: Path) -> dict[str, list[PoolEntry]]:
    """parsed/*.jsonl → {label: [PoolEntry]}（canonical + alias 全入池）。"""
    pool: dict[str, list[PoolEntry]] = {}  # label → 池条目列表
    for jsonl in sorted(parsed_dir.glob("*.jsonl")):  # 排序遍历 parsed 目录（确定性）
        if jsonl.name.endswith(".quarantine.jsonl"):  # 跳过隔离清单文件
            continue
        with jsonl.open(encoding="utf-8") as fh:  # 打开单个源 JSONL
            for line in fh:  # 逐行读取 envelope
                envelope = json.loads(line)  # 解析 JSON 行
                if envelope.get("kind") != "entity":  # 只处理实体条目（跳过关系）
                    continue
                record = envelope["record"]  # 实体记录本体
                label = envelope["label"]  # 实体类型标签
                source_id = record["provenance"]["source_id"]  # 溯源源标识
                canonical = record.get("name", "")  # 规范名
                if not canonical:  # 无名实体无法对齐
                    continue
                pool.setdefault(label, []).append(  # 规范名作为池条目入池
                    PoolEntry(
                        label=label,
                        text=canonical,  # 待比对文本 = 规范名
                        canonical=canonical,
                        source_id=source_id,
                        is_alias=False,  # 规范名条目
                    )
                )
                for alias in record.get("aliases", []) or []:  # 遍历别名列表
                    if alias and alias != canonical:  # 非空且不同于规范名
                        pool[label].append(  # 别名也作为池条目入池
                            PoolEntry(
                                label=label,
                                text=alias,  # 待比对文本 = 别名
                                canonical=canonical,  # 归属同一规范名
                                source_id=source_id,
                                is_alias=True,  # 别名条目
                            )
                        )
    # 池内去重（同源同文本只留一条；跨源保留——正是要判定的对象）
    for label in pool:  # 逐池去重
        seen: set[tuple[str, str]] = set()  # 已见 (source_id, text) 键
        deduped: list[PoolEntry] = []  # 去重结果收集
        for entry in pool[label]:  # 遍历该池全部条目
            key = (entry.source_id, entry.text)  # 去重键
            if key not in seen:  # 首次出现
                seen.add(key)  # 标记已见
                deduped.append(entry)  # 保留
        pool[label] = deduped  # 回写去重后的池
    return pool  # 返回 label → 条目列表


# ---------- C6.2 召回 ----------


def recall_topk(
    entries: list[PoolEntry],
    embedder: Embedder,
    topk: int = 10,
    batch_size: int = 64,
) -> list[list[tuple[int, float]]]:
    """池内全量召回：每个条目 → [(候选下标, sim), ...]（降序，排除自身）。

    性能：逐行 argpartition 取 top-(k+1)（含自身），O(n·k) 而非全量
    np.where 展开（32k 池曾产生数亿候选对导致 20 分钟未完成）。
    """
    texts = [e.text for e in entries]  # 待编码文本列表
    matrix = _encode_batches(embedder, texts, batch_size)  # 分批编码 → (n, dim) 矩阵
    n = len(texts)  # 池大小
    k = min(topk + 1, n)  # +1 容纳自身
    top_lists: list[list[tuple[int, float]]] = []  # 每条目的 top 候选结果
    for start in range(0, n, 1024):  # 1024 行一块（控制相似度矩阵内存）
        block = matrix[start : start + 1024] @ matrix.T  # (b, n) 块余弦相似度
        # 每行取 top-k（未排序）→ 排序 → 过滤
        part = np.argpartition(-block, kth=k - 1, axis=1)[:, :k]
        for row_in_block in range(block.shape[0]):  # 遍历块内各行
            i = start + row_in_block  # 全局行号（条目下标）
            row_sims = block[row_in_block]  # 该行与全池的相似度
            # 候选列表：(池下标, 相似度)
            cands = [(int(col), float(row_sims[col])) for col in part[row_in_block]]
            # 过滤：排除自身、低于召回线、同源同文本（内部重复对）
            cands = [
                (col, sim)
                for col, sim in cands
                if col != i
                and sim >= SIM_CANDIDATE
                and not (
                    entries[i].source_id == entries[col].source_id
                    and entries[i].text == entries[col].text
                )
            ]
            cands.sort(key=lambda t: -t[1])  # 按相似度降序排序
            top_lists.append(cands[:topk])  # 截取 top-K 个候选
    return top_lists  # 返回每条目的候选列表


def _encode_batches(embedder: Embedder, texts: list[str], batch_size: int) -> np.ndarray:
    # 分批编码后垂直拼接（避免单批超内存）
    parts = [embedder.encode(texts[i : i + batch_size]) for i in range(0, len(texts), batch_size)]
    if not parts:  # 空文本列表
        return np.zeros((0, embedder.dim), dtype=np.float32)  # 返回 (0, dim) 空矩阵
    return np.vstack(parts)  # 垂直拼接为 (n, dim)


# 召回结果 → 去重候选对（i<j 规范化；同 canonical 自身对排除）
def candidate_pairs(
    entries: list[PoolEntry],
    top_lists: list[list[tuple[int, float]]],
) -> list[CandidatePair]:
    """召回结果 → 去重候选对（i<j 规范化；同 canonical 自身对排除）。"""
    seen: set[tuple[int, int]] = set()  # 已见候选对键
    pairs: list[CandidatePair] = []  # 去重后的候选对收集
    for i, cands in enumerate(top_lists):  # 遍历每条目及其候选
        for j, sim in cands:  # 遍历该条目候选
            key = (min(i, j), max(i, j))  # i<j 规范化（对称对去重）
            if key in seen:  # 该对已处理过
                continue
            seen.add(key)  # 标记已见
            a, b = entries[key[0]], entries[key[1]]  # 取两端池条目
            if a.canonical == b.canonical and a.source_id == b.source_id:  # 同源同规范名
                continue  # 同一实体的 alias 对 canonical（内部关系，非对齐对象）
            pairs.append(CandidatePair(a=a, b=b, sim=sim))  # 收集候选对
    return pairs  # 返回去重候选对列表


# ---------- C7 精判 ----------


def _char_normalized(text: str) -> str:
    """中文常用形近归一（全半角括号/空白/标点删除）。轻量规则，不引重依赖。"""
    table = str.maketrans("（）［］【】", "()[][]")  # 全角括号 → 半角映射表
    return (  # 归一化：转括号 + 删空白与常见中文标点
        text.translate(table)  # 括号全角转半角
        .replace(" ", "")  # 去半角空格
        .replace("　", "")  # 去全角空格
        .replace("，", "")  # 去中文逗号
        .replace("。", "")  # 去中文句号
        .replace("；", "")  # 去中文分号
        .replace("：", "")  # 去中文冒号
    )


# 相似度 = 1 - levenshtein/max_len（短字符串专用）
def _edit_ratio(a: str, b: str) -> float:
    """相似度 = 1 - levenshtein/max_len（短字符串专用）。"""
    if a == b:  # 完全相同
        return 1.0  # 相似度 1
    la, lb = len(a), len(b)  # 两串长度
    if la == 0 or lb == 0:  # 任一为空串
        return 0.0  # 相似度 0
    prev = list(range(lb + 1))  # DP 前一行（初始化 0..lb）
    for i in range(1, la + 1):  # 逐行推进（a 的字符）
        cur = [i] + [0] * lb  # 当前行初始化（首列 = i）
        for j in range(1, lb + 1):  # 逐列推进（b 的字符）
            # DP 转移：删/插/替换三选一取最小
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur  # 当前行成为下一轮前一行
    return 1 - prev[lb] / max(la, lb)  # 1 - 编辑距离/最大长度


# 炮制/部位词：仅相差这些词的药名是不同实体（安全语义：生品≠炮制品，
# 巴豆/巴豆霜、天南星/制天南星、马钱子/马钱子粉 均为 toxic_exclusion L1 分界）
_PROCESS_PREFIXES = (  # 炮制/规格前缀：生品与炮制品是不同实体（L1 安全分界）
    "制",  # 制（如 制天南星/制何首乌）
    "炒",  # 炒（如 炒白术）
    "炙",  # 炙（如 炙甘草）
    "煅",  # 煅（如 煅牡蛎）
    "酒",  # 酒制（如 酒大黄）
    "盐",  # 盐制（如 盐杜仲）
    "醋",  # 醋制（如 醋香附）
    "姜",  # 姜制（如 姜半夏）
    "蜜",  # 蜜炙（如 蜜麻黄）
    "生",  # 生用（如 生石膏；生/制分界即 L1）
    "熟",  # 熟用（如 熟地黄）
    "焦",  # 焦制（如 焦山楂）
    "血",  # 血余类（如 血余炭）
    "鲜",  # 鲜品（如 鲜芦根）
)
# 部位/形态后缀
_PROCESS_SUFFIXES = ("霜", "焦", "炭", "粉", "叶", "花", "皮", "仁", "子", "母", "胶", "须", "头")


# 判两药名是否仅相差炮制前缀/后缀或部位后缀（如 巴豆/巴豆霜、珍珠/珍珠母）
def _processing_variant_diff(a: str, b: str) -> bool:
    """两药名是否仅相差炮制前缀/后缀或部位后缀。

    如 巴豆/巴豆霜、珍珠/珍珠母、何首乌/制何首乌、马钱子/马钱子粉。
    """
    if len(a) == len(b):  # 等长则不可能仅差一个前后缀
        return False
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)  # 识别短名/长名
    if longer.startswith(shorter):  # 长名以短名开头 → 差异在后缀
        return longer[len(shorter) :] in _PROCESS_SUFFIXES  # 后缀属部位/形态词表
    if longer.endswith(shorter):  # 长名以短名结尾 → 差异在前缀
        return longer[: len(longer) - len(shorter)] in _PROCESS_PREFIXES  # 前缀属炮制词表
    return False  # 其余情况不判定为炮制变体


def rule_judge(pair: CandidatePair) -> Decision:
    """规则精判（无 LLM 降级路径）——安全优先：自动合并宁缺毋滥。

    实测校准（bge-m3 + Herb 池抽查）：短中文名语义空间中"近名不同药"
    高度常见（赤芍/白芍、茯苓/猪苓、党参/明党参 sim≈0.8+），纯语义阈值
    判 same 会产生危险误合并（巴豆/巴豆霜 是 L1 生熟分界）。
    判定顺序：
    1. 归一化等价 → same 0.98（跨源同名，主要合并通道）；
    2. 炮制/部位词差异 → related（明确不自动合并）；
    3. 长名近似包含（min≥3 字，长度差≤1）→ same 0.95；
    4. 编辑距离 ≥ 0.85 且 sim ≥ 0.5 → same 0.90（4 字以上 1 字差）；
    5. 其余一律 related/different 进人工审核（C8 队列）。
    """
    a, b = _char_normalized(pair.a.text), _char_normalized(pair.b.text)  # 归一化两名称
    if a == b:  # 规则 1：归一化后字符等价（跨源同名）
        return _decide(pair, Verdict.SAME, 0.98, "字符等价（归一化后）", "rule")  # 主合并通道
    if _processing_variant_diff(a, b):  # 规则 2：炮制/部位词差异（安全分界）
        return _decide(pair, Verdict.RELATED, 0.90, "炮制/部位词差异（不同实体，安全语义）", "rule")
    # 规则 3：包含（长名含短名且 ≥3 字、长度差 ≤1）
    if (a in b or b in a) and min(len(a), len(b)) >= 3 and abs(len(a) - len(b)) <= 1:
        return _decide(pair, Verdict.SAME, 0.95, "长名近似包含", "rule")  # 长名含短名（≥3 字）
    char_sim = _edit_ratio(a, b)  # 规则 4：字符级编辑相似度
    if char_sim >= 0.85 and pair.sim >= 0.5:  # 编辑距离高且语义不低（双保险）
        return _decide(
            pair, Verdict.SAME, 0.90, f"编辑距离 {char_sim:.2f} + 语义 {pair.sim:.2f}", "rule"
        )
    if pair.sim >= 0.55:  # 规则 5：仅语义近似（近名不同药高发区）
        # 判 RELATED 送人工复核（防近名不同药误合并）
        return _decide(pair, Verdict.RELATED, 0.60, f"语义近似 {pair.sim:.2f}，需复核", "rule")
    return _decide(pair, Verdict.DIFFERENT, 0.80, f"语义差距 {pair.sim:.2f}", "rule")  # 无关


# 构造 Decision 记录（统一封装判定结果字段）
def _decide(
    pair: CandidatePair, verdict: Verdict, confidence: float, reason: str, method: str
) -> Decision:
    return Decision(  # 封装一条判定记录
        label=pair.a.label,  # 实体类型标签
        a=pair.a.canonical,  # 规范名 A
        b=pair.b.canonical,  # 规范名 B
        a_source=pair.a.source_id,  # A 的来源
        b_source=pair.b.source_id,  # B 的来源
        sim=round(pair.sim, 4),  # 语义相似度（4 位小数）
        verdict=verdict.value,  # 判定结论
        confidence=confidence,  # 置信度
        reason=reason,  # 理由
        method=method,  # 判定方式（rule/llm/human）
    )


# LLM 精判（C7.1）：注入 StructuredLLM，判 same/related/different + 理由
class LLMJudge:
    """LLM 精判（C7.1）：注入 StructuredLLM；判 same/related/different + 理由。"""

    def __init__(self, llm: Any):
        from pydantic import BaseModel, Field  # 延迟导入（定义输出 schema）

        self._llm = llm  # 注入的 StructuredLLM 实例

        class JudgeOutput(BaseModel):  # LLM 判定输出 schema
            verdict: str = Field(description="same | related | different")  # 判定结论
            confidence: float = Field(ge=0, le=1)  # 置信度（0~1）
            reason: str = Field(description="一句话理由（中文）")  # 判定理由

        self._output_cls = JudgeOutput  # 保存输出类供 extract 调用

    async def judge(self, pair: CandidatePair) -> Decision:
        system = (  # 系统提示词：对齐专家角色 + 三类结论定义
            "你是中医药实体对齐专家。判断两个名称是否指同一实体。"
            "输出 verdict∈{same,related,different}：same=同一实体（别名/异体字/全半角差异）；"
            "related=同大类但不同实体；different=无关。"
        )
        user = (  # 用户消息：实体类型 + 两名称及来源 + 召回相似度
            f"实体类型：{pair.a.label}\n"  # 实体类型标签
            f"名称A：{pair.a.text}（源：{pair.a.source_id}）\n"  # 名称 A 及其来源
            f"名称B：{pair.b.text}（源：{pair.b.source_id}）\n"  # 名称 B 及其来源
            f"语义相似度：{pair.sim:.2f}"  # 召回阶段相似度（供参考）
        )
        result = await self._llm.extract(self._output_cls, system, user)  # 结构化抽取判定
        data = result.data  # 解析出的 JudgeOutput 实例
        verdict = (  # 非法 verdict 兜底为 related（保守策略）
            data.verdict if data.verdict in {v.value for v in Verdict} else Verdict.RELATED.value
        )
        return _decide(pair, Verdict(verdict), data.confidence, data.reason, "llm")  # 封装判定


# ---------- 编排：对齐主流程 ----------


def align_label_pool(
    entries: list[PoolEntry],
    embedder: Embedder,
    topk: int = 10,
    judge: Any = None,
    auto_merge_threshold: float = 0.85,
) -> tuple[list[Decision], list[Decision], list[Decision]]:
    """单池对齐：召回 + 精判 → (全部判定, 待审核, 自动合并候选)。"""
    top_lists = recall_topk(entries, embedder, topk=topk)  # C6.2 语义召回（top-K）
    pairs = candidate_pairs(entries, top_lists)  # 召回结果 → 去重候选对
    decisions: list[Decision] = []  # 判定结果收集
    for pair in pairs:  # 逐对精判
        if judge is not None:  # 注入了 LLM 判定器（C7.1）
            import asyncio  # 延迟导入（仅 LLM 路径需要）

            decisions.append(asyncio.run(judge.judge(pair)))  # 运行异步 LLM 判定
        else:  # 无 LLM
            decisions.append(rule_judge(pair))  # 规则降级判定
    review = [  # C8 待人工审核：related 结论或置信度不足
        d
        for d in decisions
        if d.verdict == Verdict.RELATED.value or d.confidence < auto_merge_threshold
    ]
    merge = [  # 自动合并候选：same 且置信度达标（C9 加载器消费）
        d
        for d in decisions
        if d.verdict == Verdict.SAME.value and d.confidence >= auto_merge_threshold
    ]
    return decisions, review, merge  # 返回 (全部判定, 待审核, 自动合并)


def write_outputs(
    out_dir: Path,
    decisions: list[Decision],
    review: list[Decision],
    merge: list[Decision],
) -> None:
    """C7.2/C8.1 落盘：decisions.jsonl / review_queue.csv / merge_candidates.jsonl。"""
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    with (out_dir / "decisions.jsonl").open("w", encoding="utf-8") as fh:  # 全部判定文件
        for d in decisions:  # 逐条写出
            fh.write(json.dumps(d.__dict__, ensure_ascii=False) + "\n")  # dataclass 转字典落盘
    with (out_dir / "merge_candidates.jsonl").open("w", encoding="utf-8") as fh:  # 自动合并候选文件
        for d in merge:  # 逐条写出
            fh.write(json.dumps(d.__dict__, ensure_ascii=False) + "\n")  # 落盘
    # 审核队列 CSV：utf-8-sig 带 BOM（Excel 双击打开不乱码）
    with (out_dir / "review_queue.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)  # CSV 写入器
        writer.writerow(  # 表头行
            ["label", "a", "a_source", "b", "b_source", "sim", "verdict", "confidence", "reason"]
        )
        for d in review:  # 逐行写出待审核对
            writer.writerow(  # 一行一个待审实体对（人工裁决回流）
                [
                    d.label,
                    d.a,
                    d.a_source,
                    d.b,
                    d.b_source,
                    d.sim,
                    d.verdict,
                    d.confidence,
                    d.reason,
                ]
            )


# C6.3 召回评估：标注集 (a, b, 'same') 中被判定为 same 的比例
def evaluate_recall(
    decisions: list[Decision], labeled_pairs: list[tuple[str, str, str]]
) -> dict[str, float]:
    """C6.3 召回评估：标注集 (a, b, 'same') 中被判定为 same 的比例。"""
    if not labeled_pairs:  # 无标注集
        return {}  # 返回空结果
    # 判定为 same 的名称对集合（frozenset 忽略 a/b 顺序）
    predicted_same = {frozenset((d.a, d.b)) for d in decisions if d.verdict == Verdict.SAME.value}
    hit = sum(1 for a, b, _ in labeled_pairs if frozenset((a, b)) in predicted_same)  # 命中数
    # 返回：标注总数 / 判 same 命中数 / 召回率
    return {"labeled": len(labeled_pairs), "recalled_same": hit, "recall": hit / len(labeled_pairs)}
