# ===================================================================
# 文件：backend/tests/pipeline/test_load.py（pipeline 层单测 · 加载器 C9）
# 用途：验证切片构建与 L1/L2 两级安全逻辑——生品方剂整方排除（L1）、
#       含毒方剂降级 caution（L2），全部纯内存计算（Neo4j 部分 mock）
# ===================================================================

"""C9 加载器单测：切片构建 + L1/L2 安全逻辑（Neo4j 部分 mock）。"""

import json  # 标准库：写/读 JSONL
from pathlib import Path  # 标准库：tmp_path
from typing import Any  # 类型标注

import pytest  # 测试框架：fixture/monkeypatch

from tcm_kg.pipeline.load import apply_safety, build_slice, load_toxic_exclusion  # 被测函数


def _entity(label: str, name: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """辅助：构造实体 envelope（可附加属性）。"""
    return {
        "kind": "entity",  # envelope 种类：实体
        "label": label,  # 实体类型
        "record": {  # 实体记录
            "name": name,  # 名称
            "provenance": {"source_id": "src-test", "confidence": 0.95, "method": "db_import"},  # 统一溯源
            **(extra or {}),  # 附加属性（如毒性）
        },
    }


def _edge(rel_type: str, start: tuple[str, str], end: tuple[str, str]) -> dict[str, Any]:
    """辅助：构造关系 envelope（start/end 为 (label, name) 元组）。"""
    return {
        "kind": "relation",  # envelope 种类：关系
        "type": rel_type,  # 边类型
        "record": {  # 边记录
            "start": {"label": start[0], "name": start[1]},  # 起点引用
            "end": {"label": end[0], "name": end[1]},  # 终点引用
            "provenance": {"source_id": "src-test", "confidence": 0.95, "method": "db_import"},  # 溯源
        },
    }


def _write_parsed(tmp_path: Path) -> Path:
    """写一套小规模 parsed fixture：7 药 + 3 方 + 1 证 + 1 症 + 7 边（覆盖 L1/L2 场景）。"""
    parsed = tmp_path / "parsed"  # parsed 目录
    parsed.mkdir()
    lines = [  # envelope 清单
        # 基础设施
        _entity("Source", "src-test"),  # 数据源节点
        _entity("Category", "外感病类术语"),  # 类目节点
        # 种子实体
        _entity("Herb", "黄芪", {"nature": "微温", "toxicity": None}),  # 无毒药
        _entity("Herb", "甘草"),  # 无毒药
        _entity("Herb", "干姜"),  # 无毒药
        _entity("Herb", "附子", {"toxicity": "有毒"}),  # 有毒药（L2 场景）
        _entity("Herb", "半夏", {"toxicity": "有毒"}),  # 有炮制品变体（默认炮制品）
        _entity("Herb", "生半夏", {"toxicity": "有毒"}),  # 严格 L1 名单（生字头）
        _entity("Herb", "麻黄"),  # 无毒药
        _entity("Formula", "四逆汤"),  # 含附子方（L2 caution）
        _entity("Formula", "麻黄汤"),  # 无毒方（safe）
        _entity("Formula", "含生半夏方"),  # 含生品方（L1 排除）
        _entity("Pattern", "阳虚证"),  # 证候
        _entity("SymptomTCM", "恶寒"),  # 症状
        # 边
        _edge("COMPOSED_OF", ("Formula", "四逆汤"), ("Herb", "附子")),  # 四逆汤组成1
        _edge("COMPOSED_OF", ("Formula", "四逆汤"), ("Herb", "干姜")),  # 四逆汤组成2
        _edge("COMPOSED_OF", ("Formula", "四逆汤"), ("Herb", "甘草")),  # 四逆汤组成3
        _edge("COMPOSED_OF", ("Formula", "麻黄汤"), ("Herb", "麻黄")),  # 麻黄汤组成
        _edge("COMPOSED_OF", ("Formula", "含生半夏方"), ("Herb", "生半夏")),  # 违禁组成（L1）
        _edge("TREATS_PATTERN", ("Formula", "四逆汤"), ("Pattern", "阳虚证")),  # 主治证候
        _edge("HAS_SYMPTOM", ("Pattern", "阳虚证"), ("SymptomTCM", "恶寒")),  # 证候症状
    ]
    with (parsed / "src-test.jsonl").open("w", encoding="utf-8") as f:  # 写文件
        for envelope in lines:  # 逐行写
            f.write(json.dumps(envelope, ensure_ascii=False) + "\n")
    return parsed


def _write_curated(tmp_path: Path) -> Path:
    """写毒性排除清单 fixture：5 行覆盖管理品种/炮制品/生品三种形态。"""
    curated = tmp_path / "curated"  # curated 目录
    curated.mkdir()
    content = (  # CSV 文本（表头 + 5 行）
        "id,name,is_managed,is_raw,processed_variants,toxicity_grade,l1_excluded,basis\n"  # 表头
        "1,附子,true,true,黑顺片|白附片|炮附片,有毒,true,管理品种\n"  # 有炮制品变体
        "2,半夏,true,true,法半夏|姜半夏|清半夏,有毒,true,管理品种\n"  # 有炮制品变体
        "3,生半夏,true,true,,有毒,true,管理品种生品\n"  # 生字头（严格 L1）
        "4,砒石,true,true,,大毒,true,管理品种（无炮制形态）\n"  # 无炮制品（严格 L1）
        "9,制川乌,false,false,,大毒,false,炮制品\n"  # 炮制品（不触发 L1）
    )
    (curated / "toxic_exclusion.csv").write_text(content, encoding="utf-8-sig")  # 写 CSV
    return curated


@pytest.fixture
def _fixture_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """将切片种子替换为 fixture 规模（与 _write_parsed 对齐）。"""
    from tcm_kg.pipeline import load as load_mod  # 引入模块本体（改常量用）

    monkeypatch.setattr(  # 替换药材种子清单
        load_mod, "SEED_HERBS", ["黄芪", "甘草", "干姜", "附子", "半夏", "生半夏", "麻黄"]
    )
    monkeypatch.setattr(load_mod, "SEED_FORMULAS", ["四逆汤", "麻黄汤", "含生半夏方"])  # 方剂种子
    monkeypatch.setattr(load_mod, "SEED_PATTERNS", ["阳虚证"])  # 证候种子
    monkeypatch.setattr(load_mod, "SEED_SYMPTOMS", ["恶寒"])  # 症状种子


def test_build_slice_selects_seeds_and_edges(tmp_path: Path, _fixture_seeds: None) -> None:
    """切片构建：种子实体 + 端点可达的边 + 基础设施节点。"""
    parsed = _write_parsed(tmp_path)  # 准备数据
    envelopes, counts = build_slice(parsed)  # 构建切片

    assert counts["herbs"] == 7  # 7 味药材入选
    assert counts["formulas"] == 3  # 3 首方剂
    assert counts["patterns"] == 1  # 1 证候
    assert counts["symptoms"] == 1  # 1 症状
    # 全部边端点就绪
    names = {e["record"]["name"] for e in envelopes if e["kind"] == "entity"}  # 实体名集合
    for e in envelopes:  # 逐边检查
        if e["kind"] == "relation":
            assert e["record"]["start"]["name"] in names  # 起点存在（无悬空边）
            assert e["record"]["end"]["name"] in names  # 终点存在
    # 基础设施含 Source
    assert "src-test" in names  # Source 节点入切片


def test_apply_safety_l1_excludes_formula_with_raw_toxic(
    tmp_path: Path, _fixture_seeds: None
) -> None:
    """L1 严格排除：组成含'生'字头生品或无炮制形态品种 → 整方排除。"""
    parsed = _write_parsed(tmp_path)  # parsed 数据
    curated = _write_curated(tmp_path)  # curated 清单
    envelopes, _ = build_slice(parsed)  # 先建切片
    safe, stats, exclusion_rows = apply_safety(envelopes, curated)  # 应用安全防线

    # 含生半夏方被整体排除（"生半夏"在严格名单）
    formula_names = [e["record"]["name"] for e in safe if e.get("label") == "Formula"]  # 存留方剂
    assert "含生半夏方" not in formula_names  # 被排除
    assert stats.l1_excluded_formulas == 1  # 统计 1
    assert exclusion_rows[0]["formula"] == "含生半夏方"  # 排除行可审计
    assert "半夏" in exclusion_rows[0]["toxic_herbs"]  # 触发药材记录
    # 其边也排除
    edge_starts = [e["record"]["start"]["name"] for e in safe if e["kind"] == "relation"]  # 边起点
    assert "含生半夏方" not in edge_starts  # 该方的边全删
    # 四逆汤的"附子"为药典默认炮制品 → 不触发 L1（走 L2 caution）
    assert "四逆汤" in formula_names  # 四逆汤存留


def test_l1_strict_names_semantics(tmp_path: Path) -> None:
    """严格 L1 名单语义：生字头 + 无炮制形态；有炮制品变体的默认名不在名单。"""
    curated = _write_curated(tmp_path)  # curated 清单
    from tcm_kg.pipeline.load import l1_strict_names, load_toxic_exclusion  # 局部导入

    toxic = load_toxic_exclusion(curated)  # 读清单
    strict = l1_strict_names(toxic)  # 求严格名单
    assert "生半夏" in strict  # 生字头 → 在名单
    assert "砒石" in strict  # 无炮制形态 → 在名单
    assert "附子" not in strict  # 有炮制品变体（黑顺片）→ 默认付炮制品
    assert "半夏" not in strict  # 有炮制品变体
    assert "制川乌" not in strict  # 炮制品本身


def test_apply_safety_l2_caution_for_toxic_composition(
    tmp_path: Path, _fixture_seeds: None
) -> None:
    """L2：含'有毒'级成分的方剂 → safety_level=caution；无毒 → safe。"""
    parsed = _write_parsed(tmp_path)  # parsed 数据
    curated = _write_curated(tmp_path)  # curated 清单
    envelopes, _ = build_slice(parsed)  # 建切片
    safe, stats, _ = apply_safety(envelopes, curated)  # 应用防线

    safety = {  # 方剂名 → 安全级别
        e["record"]["name"]: e["record"].get("safety_level")
        for e in safe
        if e.get("label") == "Formula"
    }
    assert safety["四逆汤"] == "caution"  # 含附子（有毒级，炮制品名默认）
    assert safety["麻黄汤"] == "safe"  # 麻黄无毒
    assert stats.l2_caution_formulas == 1  # 统计 1


def test_load_toxic_exclusion(tmp_path: Path) -> None:
    """清单读取：管理品种 l1_excluded=true，炮制品 false。"""
    curated = _write_curated(tmp_path)  # curated 清单
    table = load_toxic_exclusion(curated)  # 读取为字典
    assert table["附子"]["l1_excluded"] == "true"  # 管理品种标记
    assert table["制川乌"]["l1_excluded"] == "false"  # 炮制品不排除
