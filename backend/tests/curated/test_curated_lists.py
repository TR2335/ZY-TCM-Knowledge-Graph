# ===================================================================
# 文件：backend/tests/curated/test_curated_lists.py（curated 数据单测 · 安全清单）
# 用途：校验 data/curated/ 四个人工结构化 CSV——行数/组结构/取值与权威文献逐字一致，
#       枚举与本体零偏差，L1 排除语义正确（安全红线数据不许有错）
# 覆盖验收点：C2.1/C2.2/C2.3/C2.5（双人核对前的机器自检）
# ===================================================================

"""C2 安全清单结构化校验（C2.1/C2.2/C2.3/C2.5）。

清单为人工结构化初稿（C2.6 用户抽查前），本测试保证：
- 行数/组结构/对数与权威文献一致（官方 28 种逐字核对）；
- 枚举取值与本体（Toxicity/PregnancyLevel）零偏差；
- L1 语义：管理品种 l1_excluded 全 true，炮制品全 false。
"""

import csv  # 标准库：解析 CSV 清单
from pathlib import Path  # 标准库：定位数据目录

from tcm_kg.ontology import PregnancyLevel, Toxicity  # 本体枚举（校验取值零偏差）

# 数据目录：本文件在 backend/tests/curated/ 下，往上三级到项目根再进 data/curated
_CURATED = Path(__file__).resolve().parents[3] / "data" / "curated"

# 国务院令第 23 号《医疗用毒性药品管理办法》附录·毒性中药品种（28 种，逐字）
_OFFICIAL_TOXIC_28 = [
    "砒石", "砒霜", "水银", "马钱子", "川乌", "草乌", "白附子",  # 矿物类+乌头类
    "附子", "半夏", "天南星", "巴豆", "斑蝥", "青娘虫", "红娘虫",  # 附子/天南星/虫类
    "甘遂", "狼毒", "藤黄", "千金子", "天仙子", "闹羊花", "雪上一枝蒿",  # 逐字核对清单
    "红升丹", "白降丹", "蟾酥", "洋金花", "红粉", "轻粉", "雄黄",  # 丹药/动物/矿物
]


def _rows(filename: str) -> list[dict[str, str]]:
    """辅助：读取 curated 目录下指定 CSV 为字典列表。"""
    with (_CURATED / filename).open(encoding="utf-8-sig", newline="") as f:  # utf-8-sig 兼容 BOM（Excel 导出）
        return list(csv.DictReader(f))  # 每行转字典（表头为键）


def test_shiba_fan_structure() -> None:
    """十八反：三组齐全、每组 subject/object 齐、无重名。"""
    rows = _rows("shiba_fan.csv")  # 十八反清单
    groups = {"乌头组", "甘草组", "藜芦组"}  # 官方三组（歌诀三句对应）
    assert {r["group"] for r in rows} == groups  # 实际组集合恰为三组
    for group in groups:  # 每组逐一检查
        members = [r for r in rows if r["group"] == group]  # 该组全部成员
        assert any(r["role"] == "subject" for r in members), f"{group} 缺反方"  # 必有反方（如乌头）
        assert any(r["role"] == "object" for r in members), f"{group} 缺被反方"  # 必有被反方
    names = [r["name"] for r in rows]  # 全部药名
    assert len(names) == len(set(names))  # 无重名（重名说明录入出错）
    # 歌诀句与组一一对应
    verse_by_group = {  # 官方歌诀：每组固定一句
        "乌头组": "半蒌贝蔹及攻乌",
        "甘草组": "藻戟遂芫俱战草",
        "藜芦组": "诸参辛芍叛藜芦",
    }
    for r in rows:  # 每行的歌诀必须与其所属组匹配
        assert r["verse"] == verse_by_group[r["group"]]


def test_shijiu_wei_nine_pairs() -> None:
    """十九畏：恰好 9 对，无重复。"""
    rows = _rows("shijiu_wei.csv")  # 十九畏清单
    assert len(rows) == 9  # 官方口径：9 对（十九畏是 19 味药组成 9 对）
    pairs = {frozenset((r["herb_a"], r["herb_b"])) for r in rows}  # 无序对集合（frozenset 防 A-B/B-A 重复计）
    assert len(pairs) == 9  # 9 对互不重复


def test_pregnancy_contra_levels() -> None:
    """妊娠禁忌：级别取值与本体 PregnancyLevel 零偏差，无重名。"""
    rows = _rows("pregnancy_contra.csv")  # 妊娠禁忌清单
    valid_levels = {level.value for level in PregnancyLevel} - {"无标记"}  # 合法级别（排除"无标记"——入选即有标记）
    names = [r["name"] for r in rows]  # 全部药名
    assert len(names) == len(set(names))  # 无重名
    for r in rows:  # 每行级别必须在枚举内
        assert r["level"] in valid_levels, f"{r['name']} 级别非法：{r['level']}"
    assert any(r["level"] == "禁用" for r in rows)  # 清单必须覆盖"禁用"级（如巴豆）
    assert any(r["level"] == "慎用" for r in rows)  # 清单必须覆盖"慎用"级（如桃仁）


def test_toxic_exclusion_official_28() -> None:
    """毒性排除：官方 28 种管理品种逐字齐全，L1 语义正确。"""
    rows = _rows("toxic_exclusion.csv")  # 毒性排除清单
    names = [r["name"] for r in rows]  # 全部条目名
    assert len(names) == len(set(names))  # 无重名
    managed = [r for r in rows if r["is_managed"] == "true"]  # 国家管理品种（28 种）
    assert {r["name"] for r in managed} == set(_OFFICIAL_TOXIC_28)  # 与官方名单逐字一致
    assert len(managed) == 28  # 恰 28 种
    # L1：管理品种全部排除
    assert all(r["l1_excluded"] == "true" for r in managed)  # 28 种全部触发 L1（不进方剂组成）
    # 炮制品/普通有毒药：不触发 L1（走 L2 分级）
    for r in rows:  # 非管理品种逐一检查
        if r["is_managed"] == "false":
            assert r["l1_excluded"] == "false", r["name"]  # 不触发 L1
            assert r["is_raw"] == "false", r["name"]  # 且不应标记为生品


def test_toxic_grades_match_ontology() -> None:
    """毒性分级取值与本体 Toxicity 枚举零偏差。"""
    rows = _rows("toxic_exclusion.csv")  # 毒性排除清单
    valid_grades = {g.value for g in Toxicity}  # 本体四级：无毒/小毒/有毒/大毒
    for r in rows:  # 每行分级必须在枚举内
        assert r["toxicity_grade"] in valid_grades, f"{r['name']} 分级非法"
    # 剂量隐藏矩阵口径：大毒/有毒触发掩码的品种确有登记
    grades = {r["name"]: r["toxicity_grade"] for r in rows}  # 名称 → 分级映射
    for must_be_toxic in ("川乌", "草乌", "马钱子", "斑蝥", "巴豆"):  # 代表性大毒药材
        assert grades[must_be_toxic] == "大毒"  # 必须登记为"大毒"（药典口径）


def test_l1_exclusion_semantics_for_loader() -> None:
    """L1 加载语义预演：管理品种生品名单 = 方剂排除依据。"""
    rows = _rows("toxic_exclusion.csv")  # 毒性排除清单
    l1_names = {r["name"] for r in rows if r["l1_excluded"] == "true"}  # L1 排除名单
    # 生半夏触发生品排除，但法半夏等炮制品不在 L1 名单
    assert "半夏" in l1_names  # 生半夏（管理品种）在名单
    assert "法半夏" not in l1_names  # 法半夏（炮制品）不在——炮制减毒后可用
    # 附子生品在管理品种内；黑顺片等炮制品不触发
    assert "附子" in l1_names  # 生附子在名单
    assert "黑顺片" not in l1_names  # 黑顺片（炮制品）不在
