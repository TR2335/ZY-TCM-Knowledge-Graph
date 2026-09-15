# ===== 文件注释块 =====
# 用途：BATMAN-TCM 2.0 数据解析器，方剂层规模主力数据源（54832 方）
# 所属层级：pipeline/parsers（C3 解析归一管线 BATMAN-TCM 数据源解析器）
# 核心功能：流式解析 batman_formulas.csv（525MB）产出 Formula 实体与 COMPOSED_OF 组成边；
#          herb 拼音 → 中文映射（2053 味可映射），映射不到的组成边进隔离清单；
#          规范名取 synonyms 首个中文项，无中文的方剂用拼音名（少见）。
# =====================
"""BATMAN-TCM 2.0 parser（C3.3）→ Formula + COMPOSED_OF（方剂层规模主力）。

数据（HF 镜像 f-galkin/batman2，CC BY-NC 4.0）：
- batman_herbs.csv：草药表（pref_name 为全大写拼音，synonyms 含中文名）；
- batman_formulas.csv：54832 方（pref_name 拼音，synonyms 含中文名，herbs 为拼音列表）。

策略（关键决策，与 ADR-013 联动）：
1. 规范名取中文（synonyms 首个中文项）；无中文的方剂用拼音名（少见）。
2. herb 拼音 → 中文映射（实测 2053 味可映射）；**映射不到的组成边进隔离清单**
   （不建拼音 Herb 节点，避免污染中文规范名空间、阻碍 C6 对齐）。
3. 实测边命中率约 70% → BATMAN 方剂组成普遍不完整。**安全后果**：C9 加载时
   BATMAN 源方剂一律按 safety_level=caution 处理（无法证明组成完整即无法
   证明安全，保守分级触发剂量隐藏 §7.4）——在 C9 落地，此处记录决策依据。
4. 流式 csv 读取（525MB，csv.field_size_limit 放宽，不整载 pandas）。
"""

import ast  # ast.literal_eval：安全解析 Python list 字面量字符串
import csv  # 流式读取 TSV（525MB 方剂表不整载内存）
import re  # 中文名判定正则
import sys  # sys.maxsize：放宽 csv 字段长度上限
from collections.abc import Iterator  # 迭代器类型注解（生成器产出 envelope 流）
from pathlib import Path  # 文件路径处理
from typing import Any, ClassVar  # 任意类型/类变量注解

from tcm_kg.ontology import (
    ComposedOf,  # 组成关系模型（Formula→Herb）
    EntityType,  # 实体类型枚举（NodeRef label 用）
    Formula,  # 方剂实体模型
    NodeRef,  # 关系两端节点引用
    ProvenanceMethod,  # 溯源方法枚举
    SourceType,  # 数据源类型枚举
)
from tcm_kg.pipeline.parsers.base import (
    Envelope,  # 统一信封类型别名
    SourceParser,  # 解析器抽象基类
    entity_envelope,  # 实体信封构造函数
    relation_envelope,  # 关系信封构造函数
)

_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")  # 检测文本是否含 CJK 汉字
# 中文名允许的字符（含括号、·、空格等常见形态）
_ZH_NAME = re.compile(r"^[\u4e00-\u9fff（）()\[\]、·\s]+$")  # 全串须为汉字+中文标点+空白


def is_chinese(text: str) -> bool:
    """是否为中文名（含 CJK 且仅含中文标点集）。"""
    return bool(_HAS_CJK.search(text)) and bool(_ZH_NAME.fullmatch(text))  # 双正则联合判定


def first_chinese(synonyms: list[str]) -> str | None:
    """synonyms 列表中首个中文项。"""
    for s in synonyms:  # 遍历同义词列表
        s = s.strip()  # 去首尾空白
        if is_chinese(s):  # 命中首个中文项
            return s  # 作为中文规范名返回
    return None  # 无中文项


# 将 BATMAN 的 Python list 字面量字符串解析为 list（坏行返回空列表）
def _parse_list(raw: str) -> list[str]:
    """Batman 的 Python list 字面量字符串 → list（坏行返回空）。"""
    try:
        result = ast.literal_eval(raw)  # 安全求值字面量（不执行任意代码）
    except (ValueError, SyntaxError):  # 坏行（非法字面量）
        return []  # 降级为空列表
    return result if isinstance(result, list) else []  # 非列表结果同样降级为空


class BatmanParser(SourceParser):
    """BATMAN-TCM 2.0（成分-靶点数据库，NAR 2024；经 HF 镜像获取）。"""

    source_id = "src-batman"  # 溯源 ID（Provenance.source_id）
    source_name = "BATMAN-TCM 2.0"  # 数据源显示名
    source_type = SourceType.DATABASE  # 数据库类数据源
    citation = (  # 引用（NAR 2024 论文）
        "Kong X, Liu C, Zhang Z, et al. BATMAN-TCM 2.0: an enhanced integrative "
        "database for known and predicted interactions between traditional Chinese "
        "medicine ingredients and target proteins. Nucleic Acids Research. "
        "2024;52(D1):D1110-D1120."
    )
    license_note = "CC BY-NC 4.0（禁商用）；镜像 f-galkin/batman2"  # 许可限制与获取途径

    SUBDIR: ClassVar[str] = "batman"  # 原始数据子目录
    FILE_HERBS: ClassVar[str] = "batman_herbs.csv"  # 草药表（拼音→中文映射来源）
    FILE_FORMULAS: ClassVar[str] = "batman_formulas.csv"  # 方剂表（54832 方，TSV 实为制表符分隔）

    # 解析入口：放宽 csv 字段限制，加载拼音→中文映射后流式解析方剂表
    def parse(self, raw_dir: Any) -> Iterator[Envelope]:
        csv.field_size_limit(sys.maxsize)  # 放宽字段上限（synonyms/herbs 大单元格）
        base = Path(raw_dir) / self.SUBDIR  # BATMAN 数据子目录
        yield entity_envelope(self.source_entity())  # 首个产出：本源 Source 实体
        pinyin2zh = self._load_herb_map(base / self.FILE_HERBS)  # 拼音→中文映射表
        yield from self._parse_formulas(base / self.FILE_FORMULAS, pinyin2zh)  # 流式解析方剂

    def _load_herb_map(self, path: Path) -> dict[str, str]:
        """herbs.csv → {拼音 pref_name: 中文名}（首个中文 synonym）。"""
        mapping: dict[str, str] = {}  # 拼音 → 中文映射结果
        if not path.exists():  # 草药表缺失
            return mapping  # 返回空映射（组成边将全部隔离）
        with path.open(encoding="utf-8", newline="") as f:  # 打开草药表
            for row in csv.DictReader(f, delimiter="\t"):  # 制表符分隔逐行读
                chinese = first_chinese(_parse_list(row.get("synonyms", "[]")))  # 首个中文异名
                if chinese:  # 有中文名才收录
                    mapping[row["pref_name"]] = chinese  # 记录 拼音 pref_name → 中文名
        return mapping

    def _parse_formulas(self, path: Path, pinyin2zh: dict[str, str]) -> Iterator[Envelope]:
        if not path.exists():  # 方剂表缺失
            return  # 直接返回（已产出 Source 实体）
        seen_formulas: set[str] = set()  # 已见方剂名（实体去重）
        with path.open(encoding="utf-8", newline="") as f:  # 打开方剂表（流式）
            for row in csv.DictReader(f, delimiter="\t"):  # 制表符分隔流式逐行读
                yield from self._formula_records(row, pinyin2zh, seen_formulas)  # 单行转换

    # 单行方剂记录转换：产出 Formula 实体（去重）并对每个组成药产出组成边或隔离项
    def _formula_records(
        self, row: dict[str, str], pinyin2zh: dict[str, str], seen: set[str]
    ) -> Iterator[Envelope]:
        synonyms = _parse_list(row.get("synonyms", "[]"))  # 方剂同义词列表
        chinese_name = first_chinese(synonyms)  # 首个中文项作规范名
        pinyin = (row.get("pref_name") or "").strip()  # 拼音名（pref_name 列）
        formula_name = chinese_name or pinyin  # 规范名优先中文，兜底拼音
        if not formula_name:  # 中文与拼音皆空
            return  # 跳过该行
        if formula_name not in seen:  # 方剂首次出现
            seen.add(formula_name)  # 标记已见
            yield entity_envelope(
                Formula(
                    name=formula_name,  # 中文规范名（或拼音兜底）
                    # 异名列表：去空白，且排除已作规范名的中文项
                    aliases=[s.strip() for s in synonyms if s.strip() and s != chinese_name],
                    pinyin=pinyin or None,  # 拼音（空串转 None）
                    # 溯源：db_import，置信度 0.95
                    provenance=self._prov(method=ProvenanceMethod.DB_IMPORT, confidence=0.95),
                )
            )
        # 组成边：仅映射到中文规范名的药；未映射进隔离（见模块 docstring 决策 2）
        for herb_pinyin in _parse_list(row.get("herbs", "[]")):  # 遍历组成药拼音列表
            herb_zh = pinyin2zh.get(herb_pinyin)  # 查拼音→中文映射
            if herb_zh is None:  # 映射不到：进隔离，不建拼音 Herb 节点
                yield {
                    "kind": "quarantine",  # 隔离信封
                    "source": self.source_id,  # 来源 ID
                    "reason": f"unmapped_pinyin_herb:{herb_pinyin}",  # 隔离原因（未映射拼音药）
                    "raw": {"formula": formula_name, "herb": herb_pinyin},  # 原始上下文
                }
                continue  # 不产出组成边
            yield relation_envelope(  # 映射成功：产出组成边
                ComposedOf(
                    start=NodeRef(label=EntityType.FORMULA, name=formula_name),  # 起点：方剂
                    end=NodeRef(label=EntityType.HERB, name=herb_zh),  # 终点：中文药名
                    # 溯源：db_import，置信度 0.95
                    provenance=self._prov(method=ProvenanceMethod.DB_IMPORT, confidence=0.95),
                )
            )
