# ===== 文件注释块 =====
# 用途：nmm-data 解析器，2020 药典中药材目录（616 条，对齐锚点）
# 所属层级：pipeline/parsers（C3 解析归一管线药典目录数据源解析器）
# 核心功能：解析 616 条 2020 药典目录产出 Herb 实体；作为药材规范名的对齐锚点——
#          SymMap 草药名与药典目录名在 C6 对齐时以本源为基准。
# =====================
"""nmm-data parser（C3.5）→ Herb（药典 2020 目录对齐锚点）。

数据：shennong-program/nmm-data（CC-BY-4.0），616 条 2020 药典中药材目录。
角色：药材规范名的对齐锚点——SymMap 草药名与药典目录名在 C6 对齐时以本源为基准。
"""

from collections.abc import Iterator  # 迭代器类型注解（生成器产出 envelope 流）
from typing import Any, ClassVar  # 任意类型/类变量注解

import pandas as pd  # CSV 读取（药典目录为 csv）

from tcm_kg.ontology import Herb, ProvenanceMethod, SourceType  # 药材模型/溯源方法/源类型
from tcm_kg.pipeline.parsers.base import (
    Envelope,  # 统一信封类型别名
    SourceParser,  # 解析器抽象基类
    entity_envelope,  # 实体信封构造函数
    iter_safe,  # 行级安全转换（坏行隔离）
    records,  # DataFrame → 行 dict 列表
)
from tcm_kg.pipeline.parsers.symmap import _clean  # 复用 SymMap 的单元格清洗函数


class NmmParser(SourceParser):
    """Natural Materia Medica Catalogue（2020 药典目录）。"""

    source_id = "src-nmm"  # 溯源 ID（Provenance.source_id）
    source_name = "nmm-data（2020 药典中药材目录）"  # 数据源显示名
    source_type = SourceType.PHARMACOPOEIA  # 药典类数据源（药材名对齐锚点）
    citation = "shennong-program/nmm-data, CC-BY-4.0"  # 引用（GitHub 仓库与许可）
    license_note = "CC-BY-4.0"  # 开放许可（署名即可用）

    SUBDIR: ClassVar[str] = "nmm"  # 原始数据子目录
    FILE: ClassVar[str] = "nmm-chinese-pharmacopoeia-2020.csv"  # 药典目录 CSV 文件名

    def parse(self, raw_dir: Any) -> Iterator[Envelope]:
        yield entity_envelope(self.source_entity())  # 首个产出：本源 Source 实体
        path = raw_dir / self.SUBDIR / self.FILE  # 数据文件路径
        df = pd.read_csv(path)  # 读取药典目录（616 条）
        rows = records(df)  # DataFrame → 行 dict（NaN→None）
        seen: set[str] = set()  # 药名去重集合
        for row in rows:  # 逐行处理
            # 单行包裹 iter_safe：Pydantic 校验失败即隔离该行，不中断整表
            yield from iter_safe(self.source_id, [row], lambda r: self._herb_records(r, seen))

    # 单行药材记录转换：按中文名去重后产出 Herb 实体（含拉丁名与药典引用）
    def _herb_records(self, row: dict[str, Any], seen: set[str]) -> Iterator[Envelope]:
        name = _clean(row.get("chinese_name"))  # 药材中文名（chinese_name 列）
        if not name or name in seen:  # 空名或重复跳过
            return
        seen.add(name)  # 记录已见
        yield entity_envelope(
            Herb(
                name=name,  # 药典规范名（C6 对齐锚点基准）
                latin=_clean(row.get("latin_name")),  # 拉丁学名（latin_name 列）
                standard_ref="药典2020（nmm-data 目录）",  # 规范名参照：药典 2020
                provenance=self._prov(method=ProvenanceMethod.DB_IMPORT, confidence=0.95),  # 溯源
            )
        )
