# ====================================================================
# 文件：load.py
# 用途：数据管线第四阶段（C9）——入库到 Neo4j + L1/L2 安全过滤（最核心管线文件）
# 所属层级：tcm_kg.pipeline 入库层（离线管线 Phase C 第 4 步）
# 核心功能：
#   1. 毒性排除表加载：toxic_exclusion.csv → {药名: 行}（L1/L2 判定依据）
#   2. L1 生品排除：方剂组成含生品名/无炮制形态管理品种 → 整方排除（exclusion_report.csv）
#   3. L2 风险分级：方剂含"有毒/大毒"级成分 → safety_level=caution，否则 safe
#      BATMAN 源方剂（组成不完整）一律 caution
#   4. 种子数据构建：30 Herb + 10 Formula + 10 Pattern + 20 SymptomTCM + 关联边
#      （垂直切片，验证 L1/L2/L3 安全全链路；含毒性药验安全链路）
#   5. 垂直切片选择：build_slice 三遍扫描（实体注册→边收集→端点补齐）
#   6. MERGE 语义天然幂等（跑两遍计数不变）
# 如何使用：
#   from tcm_kg.pipeline.load import load_100
#   result = load_100(parsed_dir, neo4j_repo, exclusion_report_path)
#   # 或 CLI：python -m tcm_kg.pipeline load --vertical-slice
# ====================================================================
"""C9 入库加载编排：L1 安全过滤 + L2 分级推导 + 100 条垂直切片数据集。

垂直切片（用户决策：先 100 条跑通全流程）：
- 精选种子：30 Herb（含毒性药验安全链路）+ 10 Formula + 10 Pattern + 20 SymptomTCM
  + 关联边 + 禁忌边 + Source/Category 基础设施节点；
- L1 过滤：COMPOSED_OF 终点命中 toxic_exclusion 生品名单（l1_excluded=true）
  → 该方剂整体跳过并记 exclusion_report.csv（ADR-013）；
- L2 推导：方剂含"有毒/大毒"级成分 → safety_level=caution，否则 safe；
  BATMAN 源方剂（组成不完整）一律 caution（C3.3 决策）；
- 幂等：MERGE 语义天然幂等（跑两遍计数不变）。
"""

import csv  # toxic_exclusion.csv 读取 + 排除报告写入
import json  # parsed JSONL envelope 解析
from collections.abc import Iterator  # envelope 流迭代器类型
from dataclasses import dataclass, field  # LoadStats 统计数据类
from pathlib import Path  # 目录/文件路径处理
from typing import Any  # envelope 字典通用值类型

from tcm_kg.core.logging import get_logger  # 项目统一结构化日志
from tcm_kg.graphdb.repo import Neo4jRepo  # Neo4j 入库仓库（MERGE 幂等写入）
from tcm_kg.ontology import Toxicity  # 毒性枚举（L2 分级判定用）

logger = get_logger(__name__)  # 模块级 logger

# load.py → pipeline → tcm_kg → src → backend → tcm-kgs → data/curated
CURATED_DIR = Path(__file__).resolve().parents[4] / "data" / "curated"  # 人工策划数据目录

# ---------- 垂直切片种子（~100 实体 + 关联边） ----------

# 30 味 Herb：常用药 + 毒性药（验证 L1/L2/L3 安全全链路）
SEED_HERBS = [  # 30 味种子药：24 味常用药 + 6 味毒性/安全验证药
    "黄芪",
    "人参",
    "当归",
    "白术",
    "茯苓",
    "甘草",
    "金银花",
    "连翘",
    "麻黄",
    "桂枝",
    "杏仁",
    "石膏",
    "知母",
    "黄芩",
    "黄连",
    "生地黄",
    "熟地黄",
    "白芍",
    "川芎",
    "桃仁",
    "红花",
    "大黄",
    "芒硝",
    "枳实",
    # 毒性/安全验证组
    "附子",
    "干姜",
    "炙甘草",  # 四逆汤（附子 → caution）
    "制川乌",  # 大毒炮制品（caution，不入 L1）
    "半夏",  # 生品在 L1 名单（若出现在方剂组成 → 整方跳过）
    "甘遂",  # 十八反演示端点（甘草反甘遂）
]

# 证候主/次症种子（GB/T 16751.2 口径；ADR-012 主症必配 + 次症加权）
# name → [(症状, 是否主症)]
SEED_PATTERN_SYMPTOMS: dict[str, list[tuple[str, bool]]] = {  # 证候 → [(症状, 是否主症)]
    "气虚证": [
        ("乏力", True),  # 主症（必配）
        ("气短", True),
        ("自汗", True),
        ("面色苍白", False),  # 次症（加权）
        ("心悸", False),
        ("食少", False),
    ],
    "血虚证": [
        ("面色苍白", True),
        ("头晕", True),
        ("心悸", True),
        ("失眠", False),
        ("月经不调", False),
        ("痛经", False),
    ],
    "阴虚证": [("心悸", True), ("失眠", True), ("头晕", True), ("便秘", False), ("尿黄", False)],
    "阳虚证": [
        ("乏力", True),
        ("恶寒", True),
        ("自汗", True),
        ("面色苍白", False),
        ("腹泻", False),
        ("食少", False),
    ],
    "风寒束表证": [("恶寒", True), ("发热", True), ("头痛", True), ("咳嗽", False)],
    "风热犯表证": [
        ("发热", True),
        ("头痛", True),
        ("咳嗽", True),
        ("口渴", False),
        ("尿黄", False),
    ],
    "心脾两虚": [
        ("心悸", True),
        ("失眠", True),
        ("健忘", True),
        ("乏力", True),
        ("食少", False),
        ("面色苍白", False),
    ],
    "肝肾阴虚": [("头晕", True), ("失眠", True), ("心悸", False), ("月经不调", False)],
    "脾胃虚弱": [
        ("食少", True),
        ("乏力", True),
        ("腹泻", True),
        ("腹胀", False),
        ("面色苍白", False),
    ],
    "肝郁血虚": [
        ("月经不调", True),
        ("痛经", True),
        ("头晕", True),
        ("失眠", False),
        ("腹胀", False),
    ],
}

# 方剂主治证候种子（教材标准；Formula -[TREATS]-> Pattern）
SEED_FORMULA_TREATS: dict[str, list[str]] = {  # 方剂 → 主治证候名列表
    "四逆汤": ["阳虚证"],  # 回阳救逆
    "麻黄汤": ["风寒束表证"],  # 辛温发汗
    "白虎汤": ["风热犯表证"],  # 清热生津
    "银翘散": ["风热犯表证"],  # 辛凉解表
    "四物汤": ["血虚证"],  # 补血调血
    "补中益气汤": ["气虚证"],  # 补中益气
    "归脾汤": ["心脾两虚"],  # 益气补血健脾
    "六味地黄丸": ["阴虚证"],  # 滋阴补肾
    "参苓白术散": ["脾胃虚弱"],  # 健脾益气
    "逍遥散": ["肝郁血虚"],  # 疏肝养血
}

# 方剂组成兜底种子（parsed 各源缺失时；教材标准，不给剂量）
SEED_FORMULA_COMPOSITIONS: dict[str, list[str]] = {  # 方剂 → 组成药列表（兜底）
    "归脾汤": ["白术", "当归", "茯苓", "黄芪", "远志", "龙眼肉", "酸枣仁", "人参", "木香", "甘草"],
}

# 实体源优先级（同名单标签实体：小编号覆盖大编号——BATMAN 方剂元数据粗、
# 组成混杂，openkg/curator 优先；避免 batman 先注册污染 L2 判定）
_SOURCE_PRIORITY: dict[str, int] = {  # 源标识 → 优先级（小者优先，未登记默认 5）
    "src-curator-v0": 0,  # 人工策划（最高优先）
    "src-openkg": 1,  # OpenKG 配伍金标准
    "src-nmm": 2,  # 2020 药典
    "src-symmap": 2,  # SymMap 映射库
    "src-gbt15657": 2,  # 国标分类
    "src-batman": 9,  # BATMAN（元数据粗，最低优先）
}


def _register_entity(entities: dict[str, dict[str, Any]], envelope: dict[str, Any]) -> None:
    """按源优先级注册实体；同级源字段级合并（nmm 缺性味、symmap 缺拉丁名等互补）。"""
    name = envelope["record"]["name"]  # 实体规范名（注册表主键）
    current = entities.get(name)  # 已注册的同名实体
    if current is None:  # 首次注册
        entities[name] = envelope
        return
    current_p = _SOURCE_PRIORITY.get(  # 已注册实体的源优先级（未登记源默认 5）
        current["record"].get("provenance", {}).get("source_id", ""), 5
    )
    # 新实体的源优先级（未登记源默认 5）
    new_p = _SOURCE_PRIORITY.get(envelope["record"].get("provenance", {}).get("source_id", ""), 5)
    if new_p < current_p:  # 新源优先级更高 → 整体覆盖
        entities[name] = envelope
    elif new_p == current_p:  # 同级源 → 字段级合并
        # 同级合并：已有非空字段保留，缺失字段由新实体补齐
        merged = dict(envelope["record"])  # 以新记录为基础
        for key, value in current["record"].items():  # 遍历已有记录字段
            if value is not None and value != [] and value != {}:  # 已有值非空
                merged[key] = value  # 保留已有值（新值不覆盖）
        entities[name] = {**current, "record": merged}  # 回写合并结果


# 10 首经典方剂（组成完整可验证）
SEED_FORMULAS = [
    "四逆汤",
    "麻黄汤",
    "白虎汤",
    "银翘散",
    "四物汤",
    "补中益气汤",
    "归脾汤",
    "六味地黄丸",
    "参苓白术散",
    "逍遥散",
]

# 10 个种子证候（气血阴阳虚 + 外感 + 脏腑兼证）
SEED_PATTERNS = [
    "气虚证",
    "血虚证",
    "阴虚证",
    "阳虚证",
    "风寒束表证",
    "风热犯表证",
    "心脾两虚",
    "肝肾阴虚",
    "脾胃虚弱",
    "肝郁血虚",
]

# 20 个种子症状（覆盖上述证候的全部主次症）
SEED_SYMPTOMS = [
    "心悸",
    "失眠",
    "健忘",
    "乏力",
    "气短",
    "自汗",
    "面色苍白",
    "头晕",
    "头痛",
    "发热",
    "恶寒",
    "咳嗽",
    "口渴",
    "尿黄",
    "便秘",
    "腹泻",
    "食少",
    "腹胀",
    "月经不调",
    "痛经",
]


@dataclass
class LoadStats:
    """C9.6 入库统计。"""

    entities: dict[str, int] = field(default_factory=dict)  # 实体计数：label → 数量
    relations: dict[str, int] = field(default_factory=dict)  # 关系计数：type → 数量
    l1_excluded_formulas: int = 0  # L1 排除方剂数（含生品/管理品种）
    l2_caution_formulas: int = 0  # L2 降级 caution 方剂数
    missing_endpoints: list[str] = field(default_factory=list)  # 缺失端点记录（审计用）


def load_toxic_exclusion(curated_dir: Path) -> dict[str, dict[str, str]]:
    """toxic_exclusion.csv → {药名: 行}（L1/L2 判定依据）。"""
    path = curated_dir / "toxic_exclusion.csv"  # 毒性排除表路径
    if not path.exists():  # 表不存在（未部署策划数据）
        return {}  # 返回空表（安全逻辑退化为不过滤）
    with path.open(encoding="utf-8-sig", newline="") as f:  # 带 BOM 读取（Excel 兼容）
        return {row["name"]: row for row in csv.DictReader(f)}  # {药名: 整行记录}


# ---------- envelope 流构建（从 parsed JSONL 精选） ----------


# 迭代 parsed/*.jsonl 中的 entity/relation envelope（跳过 quarantine 文件）
def _iter_parsed(parsed_dir: Path) -> Iterator[dict[str, Any]]:
    for jsonl in sorted(parsed_dir.glob("*.jsonl")):  # 排序遍历（确定性顺序）
        if jsonl.name.endswith(".quarantine.jsonl"):  # 跳过隔离清单文件
            continue
        with jsonl.open(encoding="utf-8") as fh:  # 打开源 JSONL
            for line in fh:  # 逐行读取
                envelope = json.loads(line)  # 解析 envelope
                if envelope.get("kind") in ("entity", "relation"):  # 只取实体与关系
                    yield envelope  # 流式产出（省内存）


def _synth_entity(label: str, name: str) -> dict[str, Any]:
    """合成种子实体 envelope（curator 口径，parsed 缺失时兜底）。"""
    return {  # 合成实体 envelope 结构
        "kind": "entity",  # 实体类别
        "label": label,  # Neo4j 节点标签
        "record": {
            "name": name,  # 实体规范名
            "provenance": {  # 溯源信息（人工策划口径）
                "source_id": "src-curator-v0",  # curator 源标识
                "confidence": 1.0,  # 人工策划置信度 1.0
                "extracted_at": "2026-08-24T00:00:00Z",  # 固定策划时间戳
                "method": "curated",  # 方法：人工策划
            },
        },
    }


def _synth_relation(
    rel_type: str,
    start_label: str,
    start_name: str,
    end_label: str,
    end_name: str,
    props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合成种子关系 envelope。"""
    return {  # 合成关系 envelope 结构
        "kind": "relation",  # 关系类别
        "type": rel_type,  # 关系类型（如 HAS_SYMPTOM）
        "record": {
            "start": {"label": start_label, "name": start_name},  # 起点节点引用
            "end": {"label": end_label, "name": end_name},  # 终点节点引用
            "provenance": {  # 溯源信息（人工策划口径）
                "source_id": "src-curator-v0",  # curator 源标识
                "confidence": 1.0,  # 人工策划置信度 1.0
                "extracted_at": "2026-08-24T00:00:00Z",  # 固定策划时间戳
                "method": "curated",  # 方法：人工策划
            },
            **(props or {}),  # 附加属性（如 is_primary 主次症标记）
        },
    }


def build_slice(
    parsed_dir: Path, curated_dir: Path | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """精选种子实体 + 关联边 → (envelopes, 计数)。

    三遍扫描（顺序固定，避免遍历间状态污染）：
    1. 实体注册：全部 Source + 种子名单四类（Herb/Formula/Pattern/SymptomTCM）；
       parsed 缺失的证候/症状由 curator 兜底合成；
    2. 边收集（白名单语义，按 (start,type,end) 去重）：
       - HAS_SYMPTOM / TREATS：内建种子表（GB/T 16751.2 主次症 + 方剂主治）；
       - COMPOSED_OF：种子方剂组成（openkg 源；BATMAN 同名方剂混杂大量无关组成，排除）；
       - CONTRAINDICATED_WITH / PREGNANCY_CONTRA：任一端为种子药（十八反/十九畏/妊娠）；
       - BELONGS_TO：种子证候 → Category 分类；
    3. 端点补齐：被选中边引用但未注册的实体（组成药/禁忌对端/Category）从 parsed 补全。
    """
    seed_herbs = set(SEED_HERBS)  # 种子药集合（快速命中判断）
    seed_formulas = set(SEED_FORMULAS)  # 种子方剂集合
    seed_patterns = set(SEED_PATTERNS)  # 种子证候集合
    seed_symptoms = set(SEED_SYMPTOMS)  # 种子症状集合

    # 第一遍：实体注册表（name → envelope，源优先级覆盖）
    entities: dict[str, dict[str, Any]] = {}  # 实体注册表
    for envelope in _iter_parsed(parsed_dir):  # 流式遍历 parsed
        if envelope["kind"] != "entity":  # 第一遍只看实体
            continue
        label = envelope["label"]  # 实体标签
        name = envelope["record"]["name"]  # 实体规范名
        wanted = (  # 白名单：Source 全收 + 四类种子名单命中
            label == "Source"
            or (label == "Herb" and name in seed_herbs)
            or (label == "Formula" and name in seed_formulas)
            or (label == "Pattern" and name in seed_patterns)
            or (label == "SymptomTCM" and name in seed_symptoms)
        )
        if wanted:  # 命中白名单
            _register_entity(entities, envelope)  # 按源优先级注册

    # parsed 缺失的证候/症状 → curator 合成（保证辨证链路完整）
    for name in SEED_PATTERNS:  # 遍历种子证候
        entities.setdefault(name, _synth_entity("Pattern", name))  # 缺失则合成兜底
    for name in SEED_SYMPTOMS:  # 遍历种子症状
        entities.setdefault(name, _synth_entity("SymptomTCM", name))  # 缺失则合成兜底

    # 第二遍：边收集（白名单语义）
    edges: list[dict[str, Any]] = []  # 边收集器
    seen_edges: set[tuple[str, str, str]] = set()  # 边去重键集合

    def _add_edge(edge: dict[str, Any]) -> None:  # 按 (起点,类型,终点) 去重加边
        rec = edge["record"]  # 边记录
        key = (rec["start"]["name"], edge["type"], rec["end"]["name"])  # 去重键
        if key not in seen_edges:  # 未见过该边
            seen_edges.add(key)  # 标记已见
            edges.append(edge)  # 收集

    # 内建：证候主次症 + 方剂主治 + 组成兜底
    for pattern, pairs in SEED_PATTERN_SYMPTOMS.items():  # 遍历证候主次症表
        for symptom, is_primary in pairs:  # 遍历症状（含主/次标记）
            _add_edge(  # 合成 HAS_SYMPTOM 边（证候→症状）
                _synth_relation(
                    "HAS_SYMPTOM",
                    "Pattern",
                    pattern,
                    "SymptomTCM",
                    symptom,
                    {"is_primary": is_primary},  # 主症/次症标记（ADR-012）
                )
            )
    for formula, patterns in SEED_FORMULA_TREATS.items():  # 遍历方剂主治表
        for pattern in patterns:  # 遍历主治证候
            # TREATS 边（Formula → Pattern）
            _add_edge(_synth_relation("TREATS", "Formula", formula, "Pattern", pattern))
    for formula, herbs in SEED_FORMULA_COMPOSITIONS.items():  # 遍历组成兜底表
        for herb in herbs:  # 遍历组成药
            _add_edge(_synth_relation("COMPOSED_OF", "Formula", formula, "Herb", herb))  # 组成边

    for envelope in _iter_parsed(parsed_dir):  # 再次流式遍历取 parsed 中的关系
        if envelope["kind"] != "relation":  # 只看关系
            continue
        rec = envelope["record"]  # 关系记录
        start, end = rec["start"], rec["end"]  # 两端节点引用
        src_id = rec.get("provenance", {}).get("source_id", "")  # 该边来源标识
        if (  # 白名单：种子方剂组成（排除 BATMAN）或种子药禁忌边或已注册证候分类边
            (
                envelope["type"] == "COMPOSED_OF"
                and start["name"] in seed_formulas
                and src_id != "src-batman"
            )
            or envelope["type"] in ("CONTRAINDICATED_WITH", "PREGNANCY_CONTRA")
            and (start["name"] in seed_herbs or end["name"] in seed_herbs)
            or envelope["type"] == "BELONGS_TO"
            and start["name"] in entities
        ):
            _add_edge(envelope)  # 命中白名单则收集

    # 第三遍：补齐边端点实体
    # 边引用的全部实体名 − 已注册名 = 缺失端点
    need = {ref["name"] for e in edges for ref in (e["record"]["start"], e["record"]["end"])}
    need -= entities.keys()  # 求差集
    if need:  # 存在缺失端点
        for envelope in _iter_parsed(parsed_dir):  # 再遍历 parsed
            if envelope["kind"] == "entity" and envelope["record"]["name"] in need:  # 命中缺失名
                _register_entity(entities, envelope)  # 补注册

    # 终检：边两端实体的 name 与 label 均已注册（跨源同名异标——如 gbt15657
    # 视"便秘"为病、symmap 视为症——以注册表为准，label 不一致即丢弃）
    valid_edges = [  # 仅保留两端 label 与注册表一致的边
        e
        for e in edges
        if entities.get(e["record"]["start"]["name"], {}).get("label")
        == e["record"]["start"]["label"]
        and entities.get(e["record"]["end"]["name"], {}).get("label") == e["record"]["end"]["label"]
    ]

    entity_list = list(entities.values())  # 全部注册实体列表
    counts = {  # 切片计数统计
        "entities": len(entity_list),  # 实体总数
        "relations": len(valid_edges),  # 有效边总数
        "herbs": sum(1 for e in entity_list if e["label"] == "Herb"),  # 中药数
        "formulas": sum(1 for e in entity_list if e["label"] == "Formula"),  # 方剂数
        "patterns": sum(1 for e in entity_list if e["label"] == "Pattern"),  # 证候数
        "symptoms": sum(1 for e in entity_list if e["label"] == "SymptomTCM"),  # 症状数
    }
    return entity_list + valid_edges, counts  # 返回（实体+边 混合列表，计数）


# ---------- L1 / L2 ----------


def l1_strict_names(toxic: dict[str, dict[str, str]]) -> set[str]:
    """严格 L1 名单：方剂组成中出现即整方排除。

    药典调剂规则（国务院令第 23 号第九条）："对处方未注明'生用'的毒性中药，
    应当付炮制品"——故方剂组成写规范名（附子/川乌/半夏）默认为炮制品，
    不触发 L1（走 L2 分级）。
    严格排除两类：
    1. 明确"生"字头的生品名（生川乌/生半夏/生附子…）；
    2. 无炮制品变体的管理品种（砒石/水银/斑蝥/蟾酥/雄黄…——不存在"制砒石"）。
    """
    strict: set[str] = set()  # 严格 L1 名单收集
    for name, row in toxic.items():  # 遍历毒性排除表
        if row.get("l1_excluded") != "true":  # 非排除条目跳过
            continue
        # 生品开头或无炮制形态管理品种 → 入严格名单
        if name.startswith("生") or not (row.get("processed_variants") or "").strip():
            strict.add(name)  # 入严格名单（出现即整方排除）
    return strict  # 返回严格 L1 名单


# L1 过滤 + L2 分级推导（ADR-013 三层防线的数据层）
# L1（严格）：组成含生品名/无炮制形态管理品种 → 整方排除
# L2：组成含有毒/大毒级成分 → caution；BATMAN 源方剂（组成不完整）一律 caution
# 返回 (安全 envelopes, 统计, 排除报告行)
def apply_safety(
    envelopes: list[dict[str, Any]], curated_dir: Path | None = None
) -> tuple[list[dict[str, Any]], LoadStats, list[dict[str, str]]]:
    """L1 过滤 + L2 分级推导（ADR-013 三层防线的数据层）。

    L1（严格）：组成含生品名（生XX）或无炮制形态管理品种 → 整方排除；
    L2：组成含有毒/大毒级成分（含默认炮制品名的管理品种）→ caution。
    返回 (安全 envelopes, 统计, 排除报告行)。
    """
    curated = curated_dir or CURATED_DIR.resolve()  # 策划数据目录（默认内置目录）
    toxic = load_toxic_exclusion(curated)  # 加载毒性排除表
    stats = LoadStats()  # 初始化统计

    strict_l1 = l1_strict_names(toxic)  # 严格 L1 名单（生品/无炮制形态品种）
    # 毒性等级表（含炮制品条目）
    toxicity_by_herb = {name: row["toxicity_grade"] for name, row in toxic.items()}

    # 毒性信息从 parsed Herb 实体补全（SymMap 性味解析结果）
    herb_toxicity: dict[str, str] = {}  # 药名 → 毒性等级（合并后）
    for envelope in envelopes:  # 遍历全部 envelope
        if envelope["kind"] == "entity" and envelope.get("label") == "Herb":  # Herb 实体
            tox = envelope["record"].get("toxicity")  # 实体自带毒性字段
            if tox:  # 有毒性信息
                herb_toxicity[envelope["record"]["name"]] = tox  # 记录
    # curated 表优先（药典口径）
    herb_toxicity.update({k: v for k, v in toxicity_by_herb.items() if v})  # 药典口径覆盖

    # L1：含严格生品/无炮制形态品种的方剂整体排除
    formula_composition: dict[str, list[str]] = {}  # 方剂名 → 组成药列表
    for envelope in envelopes:  # 遍历 envelope 收集组成
        if envelope["kind"] == "relation" and envelope.get("type") == "COMPOSED_OF":  # 组成边
            formula = envelope["record"]["start"]["name"]  # 方剂名（起点）
            herb = envelope["record"]["end"]["name"]  # 组成药名（终点）
            formula_composition.setdefault(formula, []).append(herb)  # 收集该方组成

    excluded_formulas: set[str] = set()  # L1 排除方剂集合
    exclusion_rows: list[dict[str, str]] = []  # 排除报告行（可审计）
    for formula, herbs in formula_composition.items():  # 遍历各方剂组成
        hits = sorted(set(herbs) & strict_l1)  # 命中严格名单的组成药
        if hits:  # 有命中 → 触发 L1
            excluded_formulas.add(formula)  # 整方排除
            stats.l1_excluded_formulas += 1  # 排除计数累加
            exclusion_rows.append(  # 记录排除详情
                {
                    "formula": formula,  # 被排除方剂名
                    "toxic_herbs": "|".join(hits),  # 命中毒性药（| 分隔）
                    "reason": "L1:含毒性管理品种生品/无炮制形态品种（国务院令第23号）",  # 法规依据
                }
            )

    # L2：剩余方剂按成分毒性分级
    result: list[dict[str, Any]] = []  # 安全过滤后的输出
    for envelope in envelopes:  # 遍历全部 envelope
        if envelope["kind"] == "entity" and envelope.get("label") == "Formula":  # 方剂实体
            name = envelope["record"]["name"]  # 方剂名
            if name in excluded_formulas:  # L1 已排除
                continue  # L1 整体跳过（实体 + 边）
            herbs = formula_composition.get(name, [])  # 该方剂组成药
            has_toxic = any(  # 是否含"有毒/大毒"级成分
                herb_toxicity.get(h) in (Toxicity.TOXIC.value, Toxicity.HIGHLY_TOXIC.value)
                for h in herbs
            )
            # BATMAN 源组成不完整（C3.3 决策）→ 一律 caution
            is_batman = envelope["record"]["provenance"]["source_id"] == "src-batman"
            safety = "caution" if (has_toxic or is_batman) else "safe"  # L2 分级结果
            if safety == "caution":  # 降级 caution
                stats.l2_caution_formulas += 1  # caution 计数累加
            record = dict(envelope["record"])  # 浅拷贝记录（不改原对象）
            record["safety_level"] = safety  # 写入安全等级字段
            result.append({**envelope, "record": record})  # 输出带等级的方剂
        elif envelope["kind"] == "relation":  # 关系边
            if envelope["record"]["start"]["name"] in excluded_formulas:  # 起点是被排除方剂
                continue  # 该边随方剂一并排除
            result.append(envelope)  # 保留其余边
        else:
            result.append(envelope)  # 其余实体（药/证候/症状等）直接保留
    return result, stats, exclusion_rows  # 返回（安全 envelopes，统计，排除报告行）


# ---------- 编排 ----------


# 垂直切片全流程：精选 → 安全过滤 → 入库 → 统计（100 条跑通全链路）
def load_100(parsed_dir: Path, repo: Neo4jRepo, exclusion_report: Path) -> dict[str, Any]:
    """垂直切片全流程：精选 → 安全过滤 → 入库 → 统计。"""
    envelopes, slice_counts = build_slice(parsed_dir)  # 精选垂直切片（三遍扫描）
    safe_envelopes, stats, exclusion_rows = apply_safety(envelopes)  # L1 过滤 + L2 分级

    # 入库
    load_stats = repo.load_envelopes(safe_envelopes)  # MERGE 批量写入 Neo4j（幂等）
    stats.entities = repo.counts_by_label()  # 图中实体计数（按 label）
    stats.relations = repo.counts_by_rel()  # 图中关系计数（按 type）

    # 排除报告（可审计，ADR-013）
    exclusion_report.parent.mkdir(parents=True, exist_ok=True)  # 确保报告目录存在
    with exclusion_report.open("w", encoding="utf-8-sig", newline="") as f:  # 带 BOM（Excel 兼容）
        writer = csv.DictWriter(f, fieldnames=["formula", "toxic_herbs", "reason"])  # 定义列
        writer.writeheader()  # 写表头
        writer.writerows(exclusion_rows)  # 写全部 L1 排除行

    return {  # 汇总执行结果
        "slice": slice_counts,  # 切片计数（构建阶段）
        "l1_excluded": stats.l1_excluded_formulas,  # L1 排除方剂数
        "l2_caution": stats.l2_caution_formulas,  # L2 caution 方剂数
        "graph_labels": stats.entities,  # 图中实体分布
        "graph_rels": stats.relations,  # 图中关系分布
        "loaded": load_stats,  # 入库写入统计
    }
