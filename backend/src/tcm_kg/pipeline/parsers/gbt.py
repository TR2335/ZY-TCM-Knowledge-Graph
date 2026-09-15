# ===== 文件注释块 =====
# 用途：GB/T 15657-2021《中医病证分类与代码》国标 PDF 解析器（病证分类主骨架）
# 所属层级：pipeline/parsers（C3 解析归一管线国家标准数据源解析器）
# 核心功能：状态机解析官方 PDF（204 页）产出病证分类树与三类术语实体——
#          A 码→DiseaseTCM（1369）、B 码→Pattern（2060）、C 码→TreatmentMethod（1168）；
#          类目词（码尾带 .）仅建 Category 导航节点；A/B 实体挂 BELONGS_TO 最近类目祖先。
# =====================
"""GB/T 15657-2021 PDF parser（C3.4）→ 病证分类树 + 疾病/证候/治法实体。

数据：中华中医药学会公开 PDF（204 页官方版，经 Internet Archive 快照获取）。
实测（scripts/probe_gbt.py 探测）：
- 疾病(A 码) 1369 = 官方数 ✓（第 10-62 页）
- 证候(B 码) 2060 = 官方数 ✓（第 63-141 页）
- 治法(C 码) 1168（附录 A，第 142-184 页）

PDF 文本形态（状态机解析）：
    A01.01.01          ← 码行（末尾带 . 为类目词）
        感冒            ← 名称行（首个为主名）
        伤风            ← 同码后续行为异名

产出：
- 类目词（码尾带 .）→ Category（name=主名, tree_path=码）——仅导航不建实体；
- A/B/C 术语 → DiseaseTCM / Pattern / TreatmentMethod（code=码, name=主名）；
- A/B 实体 → BELONGS_TO 最近类目祖先（Category）；
  治法（C）不建 BELONGS_TO：本体 §3.2 定义域为 Herb/Formula/Pattern/DiseaseTCM/Effect，
  TreatmentMethod 不在其列（零偏差原则，治法类目树仅作 Category 导航）；
- 限制：本体 DiseaseTCM/Pattern/TreatmentMethod 无 aliases 字段（设计如此），
  GB/T 同码异名暂不入库（MASTER_PLAN 记录；C6 对齐用名称+嵌入，不依赖之）。
"""

import re  # 码行/类目正则匹配
from collections.abc import Iterator  # 迭代器类型注解（生成器产出 envelope 流）
from pathlib import Path  # 文件路径处理
from typing import Any, ClassVar  # 任意类型/类变量注解

import pymupdf  # PDF 文本抽取（官方 204 页国标 PDF）

from tcm_kg.ontology import (
    BelongsTo,  # 分类归属关系模型（实体→Category）
    Category,  # 类目实体模型（分类树导航节点）
    DiseaseTCM,  # 中医疾病实体模型
    EntityType,  # 实体类型枚举（NodeRef label 用）
    NodeRef,  # 关系两端节点引用
    Pattern,  # 证候实体模型
    ProvenanceMethod,  # 溯源方法枚举
    SourceType,  # 数据源类型枚举
    TreatmentMethod,  # 治法实体模型
)
from tcm_kg.pipeline.parsers.base import (
    Envelope,  # 统一信封类型别名
    SourceParser,  # 解析器抽象基类
    entity_envelope,  # 实体信封构造函数
    relation_envelope,  # 关系信封构造函数
)

_CODE_RE = re.compile(r"^([ABC])(\d{2}(?:\.\d{2})*)\.?$")  # 码格式校验（A/B/C 前缀+两段式）
# 码行（行首码 + 可选同行主名）："B03.01.01.02.02.04 气滞痰凝证"
_CODE_LINE_RE = re.compile(r"^([ABC]\d{2}(?:\.\d{2})*\.?)\s*(.*)$")  # 捕获组 1=码、2=同行主名
# 逐页重复的表头/页眉行（不作为名称行）
_NOISE_LINES = {
    "GB/T 15657—2021",  # 标准号页眉
    "术语类目代码",  # 表头列名
    "中医疾病名术语与分类",  # 疾病表标题
    "中医证候名术语与分类",  # 证候表标题
    "中医治法名术语与分类",  # 治法表标题
}


# 按码前缀映射实体类：A→DiseaseTCM、B→Pattern、C→TreatmentMethod
def _entity_class(prefix: str) -> type[Any]:
    return {"A": DiseaseTCM, "B": Pattern, "C": TreatmentMethod}[prefix]  # 码前缀 → 实体类


class Gbt15657Parser(SourceParser):
    """GB/T 15657-2021《中医病证分类与代码》（病证分类主骨架）。"""

    source_id = "src-gbt15657"  # 溯源 ID（Provenance.source_id）
    source_name = "GB/T 15657-2021 中医病证分类与代码"  # 数据源显示名
    source_type = SourceType.NATIONAL_STANDARD  # 国家标准类数据源
    citation = (  # 引用（国标编号与发布机构）
        "GB/T 15657-2021《中医病证分类与代码》，"
        "国家市场监督管理总局、国家标准化管理委员会 2021 发布"
    )
    license_note = "国家标准（公开版 PDF，中华中医药学会官网/存档）"  # 获取途径说明

    SUBDIR: ClassVar[str] = "gbt15657"  # 原始数据子目录
    FILE: ClassVar[str] = "gbt15657-2021.pdf"  # 国标 PDF 文件名

    # 解析入口：产出 Source 实体后从 PDF 抽取条目并发射为 envelope
    def parse(self, raw_dir: Any) -> Iterator[Envelope]:
        yield entity_envelope(self.source_entity())  # 首个产出：本源 Source 实体
        path = Path(raw_dir) / self.SUBDIR / self.FILE  # 国标 PDF 路径
        if not path.exists():  # PDF 缺失
            return  # 仅产出 Source 实体
        # (码, 主名, is_category) 按文档顺序
        entries = self._extract_entries(path)  # 抽取全部条目
        yield from self._emit(entries)  # 条目 → envelope 流

    # ---------- PDF 文本 → 条目 ----------

    def _extract_entries(self, path: Path) -> list[tuple[str, str, bool]]:
        """PDF → (码, 主名, is_category) 列表。

        使用 get_text(sort=True) 的阅读序输出（实测三表区均规整）：
        - 码行以码开头，主名跟在同一行（"B03.01.01.02.02.04 气滞痰凝证"）；
        - 后续缩进行为同码异名（视觉上位于主名行下方）；
        - 跨页孤名（上页码的主名/异名）自然归给延续的 current_code。
        """
        doc = pymupdf.open(path)  # type: ignore[no-untyped-call]
        entries: list[tuple[str, str, bool]] = []  # 条目累积列表（按文档顺序）
        current_code: str | None = None  # 当前处理中的码
        names: list[str] = []  # 当前码下的名称行（首为主名，余为异名）
        for page in doc:  # type: ignore[attr-defined]
            for raw_line in page.get_text("text", sort=True).splitlines():  # 阅读序逐行
                line = raw_line.strip()  # 去首尾空白
                if not line or line in _NOISE_LINES or line.isdigit():  # 空行/页眉/纯页码
                    continue  # 不作为名称行
                matched = _CODE_LINE_RE.match(line)  # 尝试匹配码行
                if matched:  # 新码开始
                    if current_code is not None:  # 已有前一个码待收尾
                        entries.append(self._entry(current_code, names))
                    current_code = matched.group(1)  # 更新当前码
                    rest = matched.group(2).strip()  # 同行主名（可空）
                    names = [rest] if rest else []  # 名称列表初始化
                elif current_code is not None:  # 名称行（归属当前码）
                    names.append(line)  # 追加为异名
        if current_code is not None:  # 文档末尾最后一个码
            entries.append(self._entry(current_code, names))  # 收尾
        doc.close()  # type: ignore[no-untyped-call]
        # 去重（跨表重复表头理论不产生重复码，防御性保留）
        seen: set[str] = set()  # 已见码集合
        result = []  # 去重后的条目列表
        for code, name, is_cat in entries:  # 顺序遍历
            if code not in seen and name:  # 新码且主名非空
                seen.add(code)  # 标记已见
                result.append((code, name, is_cat))  # 保留该条目
        return result

    # 由码与名称列表组装单条条目：码尾带 . 为类目词，首个名称为主名
    @staticmethod
    def _entry(code: str, names: list[str]) -> tuple[str, str, bool]:
        is_category = code.endswith(".")  # 码尾带 . 为类目词（仅导航）
        primary = names[0] if names else ""  # 首个名称为主名
        return code, primary, is_category  # 组装条目三元组

    # ---------- 条目 → envelope ----------

    _LABEL_BY_PREFIX: ClassVar[dict[str, EntityType]] = {  # 码前缀 → 实体类型
        "A": EntityType.DISEASE_TCM,  # 中医疾病
        "B": EntityType.PATTERN,  # 证候
        "C": EntityType.TREATMENT_METHOD,  # 治法
    }

    # 条目 → envelope：先建类目 Category，再为各码建实体并挂 BELONGS_TO（A/B）
    def _emit(self, entries: list[tuple[str, str, bool]]) -> Iterator[Envelope]:
        prov = self._prov(method=ProvenanceMethod.CURATED, confidence=1.0)  # 国标定值溯源
        # 类目码 → Category 名（供 BELONGS_TO 溯父）
        category_names: dict[str, str] = {}  # 类目码 → 类目名映射
        for code, name, is_cat in entries:  # 第一遍：先建全部类目
            if is_cat:  # 类目词
                yield entity_envelope(
                    Category(
                        name=name,  # 类目名
                        tree_path=code,  # 层级码作树路径
                        level=code.count("."),  # 层级深度 = 码中点数
                        provenance=prov,  # 国标溯源
                    )
                )
                category_names[code] = name  # 记录供子项溯父
        for code, name, is_cat in entries:  # 第二遍：建术语实体与归属边
            if is_cat or not name:  # 类目已建；无名条目跳过
                continue
            prefix = code[0]  # 码首字母（A/B/C）
            entity_cls = _entity_class(prefix)  # 前缀 → 实体类
            yield entity_envelope(entity_cls(name=name, code=code, provenance=prov))  # 术语实体
            # 治法（C）不在 BELONGS_TO 定义域（§3.2），仅 A/B 挂分类
            if prefix == "C":  # 治法跳过挂边
                continue
            parent = self._parent_category(code, category_names)  # 最近类目祖先
            if parent is not None:  # 有祖先才建边
                yield relation_envelope(
                    BelongsTo(
                        start=NodeRef(label=self._LABEL_BY_PREFIX[prefix], name=name),  # 子项
                        end=NodeRef(label=EntityType.CATEGORY, name=parent),  # 类目祖先
                        provenance=prov,  # 国标溯源
                    )
                )

    # 求最近类目祖先：逐段去尾缩短码，命中类目码即返回其名
    @staticmethod
    def _parent_category(code: str, category_names: dict[str, str]) -> str | None:
        """最近类目祖先：逐段去尾（A01.01.01 → A01.01. → A01.）。"""
        parts = code.rstrip(".").split(".")  # 码切段（去尾点）
        while len(parts) > 1:  # 逐段去尾尝试
            parts.pop()  # 去掉最尾段
            parent_code = ".".join(parts) + "."  # 重组父类目码（带尾点）
            if parent_code in category_names:  # 命中类目码
                return category_names[parent_code]  # 返回类目名
        return None  # 无类目祖先
