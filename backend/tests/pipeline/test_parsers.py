# ===================================================================
# 文件：backend/tests/pipeline/test_parsers.py（pipeline 层单测 · 数据源解析器）
# 用途：用合成小样本 fixture 验证 6 个 parser（SymMap/OpenKG/nmm/BATMAN/GB-T/curated）——
#       解析结果与本体模型对齐、去重/隔离机制生效、真实数据对账官方计数
# 关键设计：小样本可精确断言每条产出；GB/T 与 curated 用真实数据做对账测试
# ===================================================================

"""C3 parser 单测：合成小样本 fixture，验证本体模型对齐与隔离机制。"""

import json  # 标准库：逐行解析 JSONL 产物
from pathlib import Path  # 标准库：tmp_path 夹具类型 + 真实数据目录定位

import pandas as pd  # 第三方：写 Excel/CSV fixture
import pytest  # 测试框架

from tcm_kg.ontology import ENTITY_MODELS, RELATION_MODELS, Flavor, Nature, Toxicity  # 本体注册表与枚举
from tcm_kg.pipeline.parse import run_parser  # 解析入口（统一调度 + 统计）
from tcm_kg.pipeline.parsers.batman import BatmanParser  # BATMAN 解析器
from tcm_kg.pipeline.parsers.curated import CuratedParser  # 人工清单解析器
from tcm_kg.pipeline.parsers.gbt import Gbt15657Parser  # GB/T 国标解析器
from tcm_kg.pipeline.parsers.nmm import NmmParser  # 药典目录解析器
from tcm_kg.pipeline.parsers.openkg import OpenKGParser  # OpenKG 解析器
from tcm_kg.pipeline.parsers.symmap import SymMapParser, parse_meridians, parse_properties  # SymMap 解析器 + 工具函数

# ---------- 性味/归经解析 ----------


def test_parse_properties_basic() -> None:
    """性味串基础解析："辛,微苦,平" → 药味×2 + 药性平 + 无毒。"""
    flavors, nature, toxicity = parse_properties("辛,微苦,平")  # 三元组解包
    assert flavors == [Flavor.PUNGENT, Flavor.BITTER]  # 辛+微苦（微字被清洗）
    assert nature == Nature.NEUTRAL  # 平 → NEUTRAL
    assert toxicity is None  # 无毒性词 → None


def test_parse_properties_with_toxicity() -> None:
    """带毒性的性味串：小毒/大毒分别正确识别。"""
    flavors, nature, toxicity = parse_properties("辛,苦,温,有小毒")  # 案例1：小毒
    assert flavors == [Flavor.PUNGENT, Flavor.BITTER]  # 辛苦
    assert nature == Nature.WARM  # 温
    assert toxicity == Toxicity.SLIGHT  # 有小毒 → SLIGHT

    flavors, nature, toxicity = parse_properties("辛,热,有大毒")  # 案例2：大毒
    assert flavors == [Flavor.PUNGENT]  # 只有辛
    assert nature == Nature.HOT  # 热
    assert toxicity == Toxicity.HIGHLY_TOXIC  # 有大毒 → HIGHLY_TOXIC


def test_parse_properties_empty_and_garbage() -> None:
    """容错：空输入返回空三元组；混杂垃圾词时只取合法部分。"""
    assert parse_properties(None) == ([], None, None)  # None → 全空
    assert parse_properties("") == ([], None, None)  # 空串 → 全空
    flavors, nature, toxicity = parse_properties("奇怪表述,甘,温")  # 混入垃圾词
    assert flavors == [Flavor.SWEET]  # 只识别出甘
    assert nature == Nature.WARM  # 识别出温
    assert toxicity is None  # 无毒性


def test_parse_meridians() -> None:
    """归经解析："肺,肝" → 枚举列表；None → 空列表。"""
    from tcm_kg.ontology import Meridian  # 局部导入归经枚举

    assert parse_meridians("肺,肝") == [Meridian.LUNG, Meridian.LIVER]  # 双经
    assert parse_meridians("胃,大肠") == [Meridian.STOMACH, Meridian.LARGE_INTESTINE]  # 腑经
    assert parse_meridians(None) == []  # 空输入容错


# ---------- SymMap parser ----------


def _write_symmap_fixtures(raw_dir: Path) -> None:
    """构造 SymMap 四张表的合成 Excel fixture（矮地茶/制川乌/废弃药 + 症状/证候）。"""
    out = raw_dir / "symmap"  # SymMap 数据子目录
    out.mkdir(parents=True)  # 递归创建
    # 表1 SMHB（草药表）：三行覆盖 正常/带毒/官方废弃 三种情形
    pd.DataFrame(
        [
            {  # 行1：正常药材（字段全给）
                "Herb_id": 1,  # 官方 ID
                "Chinese_name": "矮地茶",  # 中文名（规范名）
                "Pinyin_name": "Aidicha",  # 拼音
                "Latin_name": "Ardisiae Japonicae Herba",  # 拉丁学名
                "Properties_Chinese": "辛,微苦,平",  # 性味串（待解析）
                "Meridians_Chinese": "肺,肝",  # 归经串（待解析）
                "Alias": None,  # 无异名
                "Suppress": 0,  # 0=正常收录
            },
            {  # 行2：带毒药材（测毒性解析）
                "Herb_id": 2,
                "Chinese_name": "制川乌",  # 炮制品名
                "Pinyin_name": "Zhichuanwu",
                "Latin_name": None,  # 缺拉丁
                "Properties_Chinese": "辛,苦,热,有毒",  # 性味带"有毒"
                "Meridians_Chinese": "心,脾",
                "Alias": None,
                "Suppress": 0,
            },
            {  # 行3：官方废弃（应被跳过）
                "Herb_id": 3,
                "Chinese_name": "已废弃药",
                "Pinyin_name": None,
                "Latin_name": None,
                "Properties_Chinese": None,
                "Meridians_Chinese": None,
                "Alias": None,
                "Suppress": 1,  # 官方废弃 → 跳过
            },
        ]
    ).to_excel(out / SymMapParser.FILE_HERB, index=False)  # 写草药表 Excel
    # 表2 SMTS（中医症状表）：一行本义术语 + 一行同义术语（同义应跳过）
    pd.DataFrame(
        [
            {  # 本义术语：建 SymptomTCM 实体
                "TCM_symptom_id": 1,
                "TCM_symptom_name": "嗳气",
                "Symptom_pinYin": "Ai Qi",
                "Type": "Ontological terms",  # 本义
                "Suppress": 0,
            },
            {  # 同义术语：不建实体（只作别名候选）
                "TCM_symptom_id": 2,
                "TCM_symptom_name": "嗳气频作",
                "Symptom_pinYin": "Ai Qi Pin Zuo",
                "Type": "Synonymous terms",  # 同义
                "Suppress": 0,
            },
        ]
    ).to_excel(out / SymMapParser.FILE_TCM_SYMPTOM, index=False)  # 写症状表
    # 表3 SMMS（西医症状表）：一行
    pd.DataFrame(
        [{"MM_symptom_id": 1, "MM_symptom_name": "Postoperative Pain", "Suppress": 0}]
    ).to_excel(out / SymMapParser.FILE_MM_SYMPTOM, index=False)  # 写西医症状表
    # 表4 SMSY（证候表）：两行重名（测去重）
    pd.DataFrame(
        [
            {  # 第一条：正常证候
                "Syndrome_id": 1,
                "Syndrome_name": "下元虚冷",
                "Syndrome_definition": "肾阳虚证……",
                "Suppress": 0,
            },
            {  # 第二条：重名 → 去重后只留一条
                "Syndrome_id": 2,
                "Syndrome_name": "下元虚冷",
                "Syndrome_definition": "重复行",
                "Suppress": 0,
            },
        ]
    ).to_excel(out / SymMapParser.FILE_SYNDROME, index=False)  # 写证候表


def test_symmap_parser(tmp_path: Path) -> None:
    """SymMap 全流程：写 fixture → 跑解析 → 断言计数/字段/模型回验。"""
    _write_symmap_fixtures(tmp_path)  # 准备四张表
    stats = run_parser(SymMapParser, tmp_path, tmp_path / "parsed")  # 执行解析

    # 实体计数：Herb 2（Suppress=1 跳过）+ Source 1 + SymptomTCM 1（同义术语跳过）
    # + SymptomWM 1 + Pattern 1（重名去重）
    assert stats.entities == {"Source": 1, "Herb": 2, "SymptomTCM": 1, "SymptomWM": 1, "Pattern": 1}
    assert stats.quarantined == 0  # 合成样本无坏行

    # 读回产物 JSONL 逐行解析
    lines = (tmp_path / "parsed" / "src-symmap.jsonl").read_text(encoding="utf-8").splitlines()
    envelopes = [json.loads(line) for line in lines]  # envelope 列表
    herbs = [e for e in envelopes if e.get("label") == "Herb"]  # 只看药材
    assert herbs[0]["record"]["name"] == "矮地茶"  # 第一条药材名
    assert herbs[0]["record"]["flavors"] == ["辛", "苦"]  # 性味串解析为药味列表
    assert herbs[0]["record"]["nature"] == "平"  # 药性
    assert herbs[0]["record"]["meridians"] == ["肺", "肝"]  # 归经
    assert herbs[0]["record"]["provenance"]["source_id"] == "src-symmap"  # 溯源指向本源
    # 毒性标记解析（制川乌 有毒）
    assert herbs[1]["record"]["toxicity"] == "有毒"  # 毒性正确解析
    assert herbs[1]["record"]["nature"] == "热"  # 热性
    # 全部 envelope 可回验本体模型（C3 验收：校验率 100%）
    for envelope in envelopes:  # 每条产物都能通过本体模型校验
        if envelope["kind"] == "entity":  # 只验实体（关系另测）
            model = ENTITY_MODELS[envelope["label"]].model_validate(envelope["record"])
            assert model is not None  # 校验通过即非 None


# ---------- OpenKG parser ----------


def test_openkg_parser(tmp_path: Path) -> None:
    """OpenKG：方剂展开行 → 同方去重 + 组成边 + 剂量/角色属性。"""
    out = tmp_path / "openkg"  # OpenKG 数据子目录
    out.mkdir(parents=True)
    # 两行同方剂（安宫牛黄丸），一行缺剂量一行带剂量
    pd.DataFrame(
        [
            {  # 行1：蜂蜜（使药，无剂量）
                "ID": 11,
                "方剂名称": "安宫牛黄丸",
                "中药名称规范后": "蜂蜜",
                "核对后的中药剂量": None,  # 缺剂量
                "核对后的剂量单位": None,
                "地位（君臣佐使）": "使",  # 使药
            },
            {  # 行2：黄连（臣药，30 克）
                "ID": 12,
                "方剂名称": "安宫牛黄丸",  # 同一方剂
                "中药名称规范后": "黄连",
                "核对后的中药剂量": 30.0,
                "核对后的剂量单位": "克",
                "地位（君臣佐使）": "臣",  # 臣药
            },
        ]
    ).to_excel(out / OpenKGParser.FILE, index=False)  # 写 Excel
    stats = run_parser(OpenKGParser, tmp_path, tmp_path / "parsed")  # 执行解析

    # 同一方剂两行 → Formula 实体去重为 1，组成边 2 条
    assert stats.entities == {"Source": 1, "Formula": 1}  # 方剂去重
    assert stats.relations == {"COMPOSED_OF": 2}  # 两味药各一条组成边
    lines = (tmp_path / "parsed" / "src-openkg.jsonl").read_text(encoding="utf-8").splitlines()
    edges = [json.loads(line) for line in lines if json.loads(line)["kind"] == "relation"]  # 只取关系
    no_dose = next(e for e in edges if e["record"]["end"]["name"] == "蜂蜜")  # 找蜂蜜的边
    assert no_dose["record"]["dose"] is None  # 缺剂量容忍（None 而非报错）
    assert no_dose["record"]["role"] == "使"  # 角色正确
    with_dose = next(e for e in edges if e["record"]["end"]["name"] == "黄连")  # 找黄连的边
    assert with_dose["record"]["dose"] == 30.0  # 数值剂量
    assert with_dose["record"]["unit"] == "g"  # "克"→ 枚举 "g"（规范化）
    assert with_dose["record"]["role"] == "臣"  # 角色
    ComposedOf = RELATION_MODELS["ComposedOf"]  # 取组成边模型
    assert ComposedOf.model_validate(with_dose["record"]) is not None  # 边可回验本体


# ---------- nmm parser ----------


def test_nmm_parser(tmp_path: Path) -> None:
    """nmm 药典目录：两行重名（人参）→ 去重为 1 个 Herb。"""
    out = tmp_path / "nmm"  # nmm 数据子目录
    out.mkdir(parents=True)
    pd.DataFrame(
        [
            {  # 行1：人参（正常）
                "chp_index": 1,  # 药典索引
                "chinese_name": "人参",
                "latin_name": "Ginseng Radix et Rhizoma",
                "english_name": "Ginseng",
                "source_type": "plant",  # 植物药
            },
            {  # 行2：人参（重复 → 去重）
                "chp_index": 2,
                "chinese_name": "人参",
                "latin_name": "Ginseng Radix et Rhizoma",
                "english_name": "Ginseng",
                "source_type": "plant",
            },
        ]
    ).to_csv(out / NmmParser.FILE, index=False)  # 写 CSV
    stats = run_parser(NmmParser, tmp_path, tmp_path / "parsed")  # 执行解析
    # 重名去重 → 1
    assert stats.entities == {"Source": 1, "Herb": 1}  # 去重生效
    herb = json.loads(  # 读第二行（第一行是 Source）
        (tmp_path / "parsed" / "src-nmm.jsonl").read_text(encoding="utf-8").splitlines()[1]
    )
    assert herb["record"]["name"] == "人参"  # 药名
    assert herb["record"]["latin"] == "Ginseng Radix et Rhizoma"  # 拉丁学名保存


# ---------- BATMAN parser ----------


def _write_batman_fixtures(raw_dir: Path) -> None:
    """构造 BATMAN 两张 TSV fixture：草药表（映射测试）+ 方剂表（组边/隔离测试）。"""
    out = raw_dir / "batman"  # BATMAN 数据子目录
    out.mkdir(parents=True)
    # 草药表：三行——黄芪（可映射中文）、九里香（可映射）、TANG MU XUN（无中文 → 隔离）
    herbs = (
        "UID\tpref_name\tdescription\tsynonyms\tingredients\tformulas\n"  # TSV 表头
        "herbs:HUANG QI\tHUANG QI\t\t['HUANG QI', '黄芪', 'Astragalus*']\t[]\t[]\n"  # synonyms 含中文"黄芪"
        "herbs:JIU LI XIANG\tJIU LI XIANG\t\t['JIU LI XIANG', '九里香']\t[]\t[]\n"  # 含"九里香"
        "herbs:TANG MU XUN\tTANG MU XUN\t\t['TANG MU XUN', 'Thomson cowparsnip*']\t[]\t[]\n"  # 只有英文 → 无法映射
    )
    (out / "batman_herbs.csv").write_text(herbs, encoding="utf-8")  # 写草药表（实为 TSV）
    # 方剂表：三行——阿艾丸（3 味药，1 味隔离）、阿艾丸（重复行）、ZHI BAI（无中文名方剂）
    formulas = (
        "UID\tpref_name\tdescription\tsynonyms\tingredients\therbs\n"  # 表头
        "formulas:A AI WAN\tA AI WAN\t\t['阿艾丸']\t[]"  # 行1：阿艾丸 3 味
        "\t['HUANG QI', 'JIU LI XIANG', 'TANG MU XUN']\n"
        "formulas:A AI WAN\tA AI WAN\t\t['阿艾丸']\t[]\t['HUANG QI']\n"  # 行2：同 UID 重复行
        "formulas:ZHI BAI\tZHI BAI\t\t['ZHI BAI']\t[]\t['HUANG QI']\n"  # 行3：无中文方剂（拼音回退）
    )
    (out / "batman_formulas.csv").write_text(formulas, encoding="utf-8")  # 写方剂表


def test_batman_parser(tmp_path: Path) -> None:
    """拼音→中文映射、未映射隔离、方剂去重、无中文名回退拼音。"""
    _write_batman_fixtures(tmp_path)  # 准备两张表
    stats = run_parser(BatmanParser, tmp_path, tmp_path / "parsed")  # 执行解析

    # Formula：阿艾丸（去重）+ ZHI BAI（无中文回退拼音）+ Source
    assert stats.entities == {"Source": 1, "Formula": 2}  # 阿艾丸去重 + ZHI BAI 回退
    # COMPOSED_OF：行1（黄芪+九里香 命中，TANG MU XUN 隔离）+ 行2（重复方剂，黄芪再出边）
    # + 行3（ZHI BAI 方，黄芪）= 4 条有效
    assert stats.relations == {"COMPOSED_OF": 4}  # 2+1+1=4
    assert stats.quarantined == 1  # TANG MU XUN 一条进隔离清单

    # 读回产物验证字段
    lines = (tmp_path / "parsed" / "src-batman.jsonl").read_text(encoding="utf-8").splitlines()
    envelopes = [json.loads(line) for line in lines]  # 全部 envelope
    formulas = [e for e in envelopes if e.get("label") == "Formula"]  # 方剂实体
    by_name = {f["record"]["name"]: f["record"] for f in formulas}  # 名称索引
    assert by_name["阿艾丸"]["pinyin"] == "A AI WAN"  # 拼音保存
    assert "九里香" not in by_name["阿艾丸"]["aliases"]  # 中文名不重复入 aliases（name 本身即是）
    assert by_name["ZHI BAI"]["name"] == "ZHI BAI"  # 拼音回退（无中文可用）
    # 隔离清单内容
    q_lines = (
        (tmp_path / "parsed" / "src-batman.quarantine.jsonl")  # 隔离文件与产物同目录
        .read_text(encoding="utf-8")
        .splitlines()
    )
    quarantines = [json.loads(line) for line in q_lines]  # 隔离记录
    assert quarantines[0]["reason"] == "unmapped_pinyin_herb:TANG MU XUN"  # 隔离原因：拼音无法映射
    assert quarantines[0]["raw"]["formula"] == "阿艾丸"  # 原始上下文保留（可追溯）


def test_batman_is_chinese() -> None:
    """中文字符判定工具：is_chinese / first_chinese。"""
    from tcm_kg.pipeline.parsers.batman import first_chinese, is_chinese  # 局部导入工具函数

    assert is_chinese("黄芪")  # 纯中文 → True
    assert is_chinese("（而火）炷丹")  # 含中文标点也算（首字符是中文括号）
    assert not is_chinese("HUANG QI")  # 拼音 → False
    assert not is_chinese("Astragalus*")  # 英文+星号 → False
    assert first_chinese(["HUANG QI", "黄芪", "Astragalus*"]) == "黄芪"  # 列表中第一个中文
    assert first_chinese(["A", "B"]) is None  # 无中文 → None


# ---------- GB/T 15657 parser（真实 PDF，读项目 data/raw/gbt15657） ----------


def lines_of(tmp_path: Path) -> list[str]:
    """辅助：读取解析产物 src-gbt15657.jsonl 的全部行。"""
    return (tmp_path / "src-gbt15657.jsonl").read_text(encoding="utf-8").splitlines()


def _category_count(lines: list[str], prefix: str) -> int:
    """某标识符前缀（A/B/C）的类目 Category 数（tree_path 前缀匹配）。"""
    count = 0
    for line in lines:  # 遍历产物行
        envelope = json.loads(line)  # 解析 envelope
        if envelope.get("label") == "Category" and envelope["record"]["tree_path"].startswith(
            prefix
        ):  # 是类目且码前缀匹配
            count += 1  # 计数
    return count


def test_gbt_parser_real_pdf(tmp_path: Path) -> None:
    """真实 GB/T PDF：码数与官方一致（A 1369 / B 2060 / C 1168），树/边语义正确。"""
    raw_source = Path(__file__).resolve().parents[3] / "data" / "raw"  # 项目真实原始数据目录
    gbt_dir = raw_source / "gbt15657"  # GB/T 子目录
    if not (gbt_dir / Gbt15657Parser.FILE).exists():  # PDF 未下载则跳过（CI 友好）
        pytest.skip("GB/T PDF 未下载（tcm download）")

    stats = run_parser(Gbt15657Parser, raw_source, tmp_path)  # 解析真实 PDF
    # 官方计数口径（前言）：1369 病（含 113 类目词）/ 2060 证候（含 406 类目词）
    # → 类目词建 Category、其余建实体，两者之和与官方总数对账
    assert stats.entities["DiseaseTCM"] + _category_count(lines_of(tmp_path), "A") == 1369  # A 码对账
    assert stats.entities["Pattern"] + _category_count(lines_of(tmp_path), "B") == 2060  # B 码对账
    assert stats.entities["TreatmentMethod"] + _category_count(lines_of(tmp_path), "C") == 1168  # C 码对账
    assert stats.entities["Category"] > 400  # 类目词数百个（B 码 406+）
    # A/B 实体挂 BELONGS_TO（治法 C 码不在定义域）
    assert stats.relations["BELONGS_TO"] == stats.entities["DiseaseTCM"] + stats.entities["Pattern"]  # 每实体一条
    assert stats.quarantined == 0  # 官方 PDF 解析零坏行

    # 抽样验证树路径与边语义
    lines = lines_of(tmp_path)  # 产物行
    envelopes = [json.loads(line) for line in lines]  # 解析
    diseases = {  # 中医疾病索引
        e["record"]["name"]: e["record"] for e in envelopes if e.get("label") == "DiseaseTCM"
    }
    assert diseases["感冒"]["code"] == "A01.01.01"  # 抽查码
    assert diseases["暑脱"]["code"] == "A01.01.02.03.01"  # 深层码
    belongs = [e for e in envelopes if e.get("type") == "BELONGS_TO"]  # 归类边
    ganmao = next(e for e in belongs if e["record"]["start"]["name"] == "感冒")  # 感冒的归类边
    assert ganmao["record"]["end"]["name"] == "外感时令类病"  # 挂到正确类目
    assert ganmao["record"]["start"]["label"] == "DiseaseTCM"  # 起点类型正确
    patterns = {e["record"]["name"]: e["record"] for e in envelopes if e.get("label") == "Pattern"}  # 证候索引
    assert patterns["气滞痰凝证"]["code"] == "B03.01.01.02.02.04"  # 抽查深层 B 码
    methods = {  # 治法索引
        e["record"]["name"]: e["record"] for e in envelopes if e.get("label") == "TreatmentMethod"
    }
    # 实体数 = 1168 - 治法类目数（C 码带点条目建 Category）
    assert len(methods) == stats.entities["TreatmentMethod"]  # 自洽
    assert methods["理气健脾"]["code"].startswith("C02.")  # 抽查 C 码


# ---------- curated parser（真实清单，读项目 data/curated） ----------


def test_curated_parser_real_lists(tmp_path: Path) -> None:
    """真实 curated 清单：十八反 87 对 + 十九畏 7 对（犀角×2 跳过）+ 妊娠 73。"""
    # raw_dir.parent 需指向含 curated/ 的目录：借 data/raw 结构
    raw_dir = tmp_path / "raw"  # 伪 raw 目录
    curated = tmp_path / "curated"  # 伪 curated 目录（parser 从 raw_dir.parent 找它）
    raw_dir.mkdir()  # 建目录
    curated.mkdir()
    curated_source = Path(__file__).resolve().parents[3] / "data" / "curated"  # 项目真实清单
    for csv_name in ("shiba_fan.csv", "shijiu_wei.csv", "pregnancy_contra.csv"):  # 三张清单
        (curated / csv_name).write_text(  # 复制到 tmp（不污染真实数据）
            (curated_source / csv_name).read_text(encoding="utf-8-sig"), encoding="utf-8-sig"
        )

    stats = run_parser(CuratedParser, raw_dir, tmp_path / "parsed")  # 执行解析
    # 十八反：乌头组 5×13 + 甘草组 2×5 + 藜芦组 1×12 = 87
    assert stats.relations["CONTRAINDICATED_WITH"] == 87 + 7  # 十八反 87 + 十九畏 7
    assert stats.relations["PREGNANCY_CONTRA"] == 73  # 妊娠禁忌 73 条
    assert stats.entities == {"Source": 1, "Category": 1}  # 产 1 源 + 1 妊娠禁忌类目
    assert stats.quarantined == 0  # 清单干净

    # 逐边验证语义
    lines = (tmp_path / "parsed" / "src-curator-v0.jsonl").read_text(encoding="utf-8").splitlines()
    edges = [json.loads(line) for line in lines]  # 全部产物
    contra = [e for e in edges if e.get("type") == "CONTRAINDICATED_WITH"]  # 禁忌边
    shifan = [e for e in contra if e["record"]["type"] == "十八反"]  # 十八反子集
    shijiu = [e for e in contra if e["record"]["type"] == "十九畏"]  # 十九畏子集
    assert len(shifan) == 87  # 十八反对数
    assert len(shijiu) == 7  # 十九畏对数（9 对中犀角×2 被跳过）
    # 犀角对跳过
    assert all(
        "犀角" not in (e["record"]["start"]["name"], e["record"]["end"]["name"]) for e in shijiu
    )  # 药典未收载 → 不建边
    # 异名归一：朴硝 → 芒硝
    assert any(e["record"]["end"]["name"] == "芒硝" for e in shijiu)  # 归一化生效
    # 妊娠边指向固定 Category
    preg = [e for e in edges if e.get("type") == "PREGNANCY_CONTRA"]  # 妊娠边
    assert all(e["record"]["end"]["name"] == "妊娠禁忌" for e in preg)  # 终点统一
    assert all(e["record"]["level"] in ("禁用", "忌用", "慎用") for e in preg)  # 级别合法
    # 全部边可回验本体模型
    for e in edges:  # 逐条回验
        if e["kind"] == "relation":
            model = RELATION_MODELS[
                "ContraindicatedWith" if e["type"] == "CONTRAINDICATED_WITH" else "PregnancyContra"
            ]  # 按类型选模型
            assert model.model_validate(e["record"]) is not None  # 校验通过
