# ===== 文件注释块 =====
# 用途：SymMap v2.0 整合数据库解析器（症状映射，NAR 2019）
# 所属层级：pipeline/parsers（C3 解析归一管线 SymMap 数据源解析器）
# 核心功能：解析四张表产出四类实体——SMHB→Herb（698，含性味归经毒性解析）、
#          SMTS→SymptomTCM（仅本义术语）、SMMS→SymptomWM、SMSY→Pattern（233）；
#          性味串解析 parse_properties（"辛,苦,温,有小毒"→药味/药性/毒性三分离）。
# =====================
"""SymMap v2.0 parser（C3.2）→ Herb / SymptomTCM / SymptomWM / Pattern。

文件与映射（data/raw/symmap/，来源 symmap.org v2.0 下载页）：
- SMHB → Herb（698 行有效；性味串内嵌毒性标记，如"辛,苦,温,有小毒"）
- SMTS → SymptomTCM（仅 Ontological terms；Synonymous terms 为异名，本文件无
  归属目标，留待 C6 对齐阶段处理）
- SMMS → SymptomWM（英文名，SymMap 口径即英文 UMLS 系）
- SMSY → Pattern（233 证候）
- SMDE（西医疾病）暂缓：SymMap 提供 ICD-10 码，而本体 DiseaseWM 必填 ICD-11，
  需 ICD-API 桥接后入库（C6/二阶段），本阶段不产出。

行过滤：Suppress != 0 的行为官方废弃术语，一律跳过。
"""

from collections.abc import Iterator  # 迭代器类型注解（生成器产出 envelope 流）
from typing import Any, ClassVar  # 任意类型/类变量注解

import pandas as pd  # Excel 读取与 NaN 判定（SymMap 四张表均为 xlsx）

from tcm_kg.ontology import (
    Flavor,  # 药味枚举（辛甘酸苦咸等）
    Herb,  # 药材实体模型
    Meridian,  # 归经枚举（脏腑经络定向）
    Nature,  # 药性枚举（寒热温凉平）
    Pattern,  # 证候实体模型
    SourceType,  # 数据源类型枚举
    SymptomTCM,  # 中医症状实体模型
    SymptomWM,  # 西医症状实体模型
    Toxicity,  # 毒性枚举（小毒/有毒/大毒）
)
from tcm_kg.pipeline.parsers.base import (
    Envelope,  # 统一信封类型别名
    SourceParser,  # 解析器抽象基类
    entity_envelope,  # 实体信封构造函数
    iter_safe,  # 行级安全转换（坏行隔离）
    records,  # DataFrame → 行 dict 列表
)

_NATURES = {n.value for n in Nature}  # 药性取值集合（token 快速查表）
_FLAVORS = {f.value for f in Flavor}  # 药味取值集合
_MERIDIANS = {m.value for m in Meridian}  # 归经取值集合
# 性味串中的毒性标记 → 本体 Toxicity
_TOXICITY_TOKENS = {
    "有毒": Toxicity.TOXIC,  # 有毒档
    "有小毒": Toxicity.SLIGHT,  # 小毒档
    "有大毒": Toxicity.HIGHLY_TOXIC,  # 大毒档
}


def parse_properties(raw: str | None) -> tuple[list[Flavor], Nature | None, Toxicity | None]:
    """性味串 → (药味, 药性, 毒性)。

    样例：
        "辛,微苦,平"       → ([辛, 苦], 平, None)
        "辛,苦,温,有小毒"  → ([辛, 苦], 温, 小毒)
        "辛,热,有大毒"     → ([辛], 热, 大毒)
    规则：毒性标记（有毒/有小毒/有大毒）单独识别；"微X"药味归一为 X
    （本体 Flavor 无微味档，微寒/微温为药性不受影响）；无法识别的 token 丢弃。
    """
    flavors: list[Flavor] = []  # 药味列表（可多值）
    nature: Nature | None = None  # 药性（单值，后写覆盖）
    toxicity: Toxicity | None = None  # 毒性（单值）
    if not raw or pd.isna(raw):  # 空值/NaN 输入
        return flavors, nature, toxicity  # 三项皆空
    for token in str(raw).replace("、", ",").replace("；", ",").split(","):  # 分隔符归一后切分
        token = token.strip()  # 去首尾空白
        if not token:
            continue  # 跳过空 token
        if token in _TOXICITY_TOKENS:  # 命中毒性标记
            toxicity = _TOXICITY_TOKENS[token]  # 记录毒性档位
            continue
        if token in _NATURES:  # 命中药性
            nature = Nature(token)  # 记录药性
            continue
        if token in _FLAVORS:  # 命中药味
            flavors.append(Flavor(token))  # 追加药味（可多值）
            continue
        if token.startswith("微") and token[1:] in _FLAVORS:  # "微X"味归一为 X
            flavors.append(Flavor(token[1:]))  # 本体无微味档，取基础味
    return flavors, nature, toxicity  # 解析结果三元组


def parse_meridians(raw: str | None) -> list[Meridian]:
    """归经串 → [Meridian]；无法识别的 token 丢弃。"""
    if not raw or pd.isna(raw):  # 空值/NaN 输入
        return []  # 无归经信息
    result: list[Meridian] = []  # 归经结果列表
    for token in str(raw).replace("、", ",").split(","):  # 顿号归一后切分
        token = token.strip().rstrip("经")  # 去空白并去"经"字后缀（"肝经"→"肝"）
        if token in _MERIDIANS:  # 命中归经枚举值
            result.append(Meridian(token))  # 收录（无法识别的丢弃）
    return result


# pandas 单元格清洗：NaN/空值 → None，非空 → 去首尾空白后的字符串
def _clean(value: object) -> str | None:
    """pandas 单元格 → 干净字符串（NaN/空 → None）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


class SymMapParser(SourceParser):
    """SymMap v2.0（症状映射整合数据库，NAR 2019）。"""

    source_id = "src-symmap"  # 溯源 ID（Provenance.source_id）
    source_name = "SymMap v2.0"  # 数据源显示名
    source_type = SourceType.DATABASE  # 数据库类数据源
    citation = (  # 引用文献（NAR 2019 论文）
        "Wu Y, Zhang F, Yang K, et al. SymMap: an integrative database of traditional "
        "Chinese medicine enhanced by symptom mapping. Nucleic Acids Research. "
        "2019;47(D1):D1110-D1117."
    )
    license_note = "机构版权（北中医/中科院计算所/北交大），学术引用使用"  # 许可限制说明

    SUBDIR: ClassVar[str] = "symmap"  # 原始数据子目录
    FILE_HERB: ClassVar[str] = "symmap_v2_SMHB.xlsx"  # 药材表（SMHB）
    FILE_TCM_SYMPTOM: ClassVar[str] = "symmap_v2_SMTS.xlsx"  # 中医症状表（SMTS）
    FILE_MM_SYMPTOM: ClassVar[str] = "symmap_v2_SMMS.xlsx"  # 西医症状表（SMMS）
    FILE_SYNDROME: ClassVar[str] = "symmap_v2_SMSY.xlsx"  # 证候表（SMSY）

    # 解析入口：产出 Source 实体后依次解析药材/中医症状/西医症状/证候四张表
    def parse(self, raw_dir: Any) -> Iterator[Envelope]:
        base = raw_dir / self.SUBDIR  # SymMap 数据子目录
        yield entity_envelope(self.source_entity())  # 首个产出：本源 Source 实体
        yield from self._parse_herbs(base / self.FILE_HERB)  # SMHB → Herb
        yield from self._parse_tcm_symptoms(base / self.FILE_TCM_SYMPTOM)  # SMTS → SymptomTCM
        yield from self._parse_mm_symptoms(base / self.FILE_MM_SYMPTOM)  # SMMS → SymptomWM
        yield from self._parse_syndromes(base / self.FILE_SYNDROME)  # SMSY → Pattern

    # ---------- SMHB → Herb ----------

    def _parse_herbs(self, path: Any) -> Iterator[Envelope]:
        df = pd.read_excel(path)  # 读取 SMHB 药材表
        active = df[df["Suppress"] == 0]  # 过滤官方废弃术语（Suppress != 0）
        rows = records(active)  # DataFrame → 行 dict（NaN→None）
        seen: set[str] = set()  # 药名去重集合
        for row in rows:  # 逐行处理
            # 单行包裹 iter_safe：Pydantic 校验失败即隔离该行，不中断整表
            yield from iter_safe(self.source_id, [row], lambda r: self._herb_records(r, seen))

    def _herb_records(self, row: dict[str, Any], seen: set[str]) -> Iterator[Envelope]:
        name = _clean(row["Chinese_name"])  # 药材中文名（SMHB Chinese_name 列）
        if not name:  # 无名行跳过
            return
        if name in seen:  # 重复药名跳过（同表多行同名）
            return
        seen.add(name)  # 记录已见药名
        flavors, nature, toxicity = parse_properties(row.get("Properties_Chinese"))  # 性味串三分离
        alias_raw = _clean(row.get("Alias"))  # 别名原始串（顿号/逗号分隔）
        yield entity_envelope(
            Herb(
                name=name,  # 药材规范名
                # 别名切分：顿号归一为逗号，逐项去空白后滤空串
                aliases=[a.strip() for a in alias_raw.replace("、", ",").split(",") if a.strip()]
                if alias_raw  # 有别名才切分
                else [],  # 无别名给空列表
                pinyin=_clean(row.get("Pinyin_name")),  # 拼音（Pinyin_name 列）
                latin=_clean(row.get("Latin_name")),  # 拉丁学名（Latin_name 列）
                nature=nature,  # 药性
                flavors=flavors,  # 药味列表
                meridians=parse_meridians(row.get("Meridians_Chinese")),  # 归经串解析
                toxicity=toxicity,  # 毒性档位
                standard_ref="SymMap v2.0",  # 规范名参照来源标记
                provenance=self._prov(),  # 溯源：db_import 0.95
            )
        )

    # ---------- SMTS → SymptomTCM ----------

    def _parse_tcm_symptoms(self, path: Any) -> Iterator[Envelope]:
        df = pd.read_excel(path)  # 读取 SMTS 中医症状表
        # 仅本义术语建实体；同义术语（Synonymous terms）为异名，本文件无归属目标
        active = df[(df["Suppress"] == 0) & (df["Type"] == "Ontological terms")]
        rows = records(active)  # DataFrame → 行 dict（NaN→None）
        seen: set[str] = set()  # 症状名去重集合
        for row in rows:  # 逐行处理
            yield from iter_safe(  # 单行安全转换：坏行进隔离清单
                self.source_id, [row], lambda r: self._symptom_tcm_records(r, seen)
            )

    def _symptom_tcm_records(self, row: dict[str, Any], seen: set[str]) -> Iterator[Envelope]:
        name = _clean(row["TCM_symptom_name"])  # 中医症状名（SMTS 主名列）
        if not name or name in seen:  # 空名或重复跳过
            return
        seen.add(name)  # 记录已见
        yield entity_envelope(
            SymptomTCM(
                name=name,  # 症状规范名
                pinyin=_clean(row.get("Symptom_pinYin")),  # 拼音（Symptom_pinYin 列）
                provenance=self._prov(),  # 溯源：db_import 0.95
            )
        )

    # ---------- SMMS → SymptomWM ----------

    # 解析 SMMS 西医症状表：过滤废弃术语后逐行转换为 SymptomWM 记录
    def _parse_mm_symptoms(self, path: Any) -> Iterator[Envelope]:
        df = pd.read_excel(path)  # 读取 SMMS 西医症状表
        active = df[df["Suppress"] == 0]  # 过滤官方废弃术语
        rows = records(active)  # DataFrame → 行 dict（NaN→None）
        seen: set[str] = set()  # 症状名去重集合
        for row in rows:  # 逐行处理
            # 单行安全转换：坏行进隔离清单（同 _parse_herbs 模式）
            yield from iter_safe(self.source_id, [row], lambda r: self._symptom_wm_records(r, seen))

    def _symptom_wm_records(self, row: dict[str, Any], seen: set[str]) -> Iterator[Envelope]:
        name = _clean(row["MM_symptom_name"])  # 西医症状名（SMMS 主名列，英文口径）
        if not name or name in seen:  # 空名或重复跳过
            return
        seen.add(name)  # 记录已见
        # SymMap 西医症状口径即英文 UMLS 系，en_name 与 name 同列取值
        yield entity_envelope(SymptomWM(name=name, en_name=name, provenance=self._prov()))

    # ---------- SMSY → Pattern ----------

    def _parse_syndromes(self, path: Any) -> Iterator[Envelope]:
        df = pd.read_excel(path)  # 读取 SMSY 证候表
        active = df[df["Suppress"] == 0]  # 过滤官方废弃术语
        rows = records(active)  # DataFrame → 行 dict（NaN→None）
        seen: set[str] = set()  # 证候名去重集合
        for row in rows:  # 逐行处理
            # 单行安全转换：坏行进隔离清单（同 _parse_herbs 模式）
            yield from iter_safe(self.source_id, [row], lambda r: self._pattern_records(r, seen))

    # 单行证候记录转换：去重后产出 Pattern 实体
    def _pattern_records(self, row: dict[str, Any], seen: set[str]) -> Iterator[Envelope]:
        name = _clean(row["Syndrome_name"])  # 证候名（SMSY Syndrome_name 列）
        if not name or name in seen:  # 空名或重复跳过
            return
        seen.add(name)  # 记录已见
        yield entity_envelope(Pattern(name=name, provenance=self._prov()))  # 证候实体
