# ===== 文件注释块 =====
# 用途：人工整理种子数据解析器，解析 curator 口径的精选药材/方剂/证候数据（优先级最高 0）
# 所属层级：pipeline/parsers（C3 解析归一管线人工种子数据解析器）
# 核心功能：解析人工结构化安全清单（十八反/十九畏/妊娠禁忌），产出 CONTRAINDICATED_WITH
#          与 PREGNANCY_CONTRA 禁忌边，作为图谱安全红线数据源（ADR-013）。
# =====================
"""curated 安全清单 parser（C3.5）→ 禁忌边（安全红线，ADR-013）。

data/curated/ 四个清单（C2 人工结构化，C2.6 抽查前为 v0）：
- shiba_fan.csv       → CONTRAINDICATED_WITH（十八反，组内 subject × object 全配对）
- shijiu_wei.csv      → CONTRAINDICATED_WITH（十九畏；犀角对药典未收载 → 跳过）
- pregnancy_contra.csv→ PREGNANCY_CONTRA（Herb → Category('妊娠禁忌')，level 三级）
- toxic_exclusion.csv → 不产边（L1 过滤依据，由 C9 加载器消费）

异名归一：朴硝/牙硝→芒硝、官桂→肉桂（见 shijiu_wei.csv note 列）。
"""

import csv  # CSV 读取（清单文件，utf-8-sig 兼容 BOM）
from collections.abc import Iterator  # 迭代器类型注解（生成器产出 envelope 流）
from typing import Any, ClassVar  # 任意类型/类变量注解

from tcm_kg.ontology import (
    PREGNANCY_CATEGORY_NAME,  # 妊娠禁忌类目名常量（安全红线挂靠节点）
    Category,  # 类目实体模型（妊娠禁忌 Category）
    ContraindicatedWith,  # 配伍禁忌关系模型（安全红线边）
    ContraType,  # 禁忌类型枚举（十八反/十九畏）
    EntityType,  # 实体类型枚举（NodeRef label 用）
    NodeRef,  # 关系两端节点引用
    PregnancyContra,  # 妊娠禁忌关系模型（安全红线边）
    PregnancyLevel,  # 妊娠禁忌分级枚举（禁用/慎用）
    ProvenanceMethod,  # 溯源方法枚举
    SourceType,  # 数据源类型枚举
)
from tcm_kg.pipeline.parsers.base import (
    Envelope,  # 统一信封类型别名
    SourceParser,  # 解析器抽象基类
    entity_envelope,  # 实体信封构造函数
    relation_envelope,  # 关系信封构造函数
)

# 十九畏中的异名 → 图谱规范名
# 安全语义：禁忌边必须挂在规范名节点上，异名不归一会导致禁忌对漏检
_NAME_NORMALIZE = {"朴硝": "芒硝", "牙硝": "芒硝", "官桂": "肉桂"}
# 犀角：药典未收载（1993 年起禁用），图谱不建节点 → 相关禁忌对仅存文档；
# 密陀僧虽药典未收载，但按 note 保留文献级禁忌边
_SKIP_HERBS = {"犀角"}  # 药典未收载药材，跳过不建禁忌边


class CuratedParser(SourceParser):
    """C2 人工结构化清单（v0，双人核对 C2.6 通过后升 v1）。"""

    source_id = "src-curator-v0"  # 溯源 ID（v0，C2.6 抽查通过后升 v1）
    source_name = "人工整理安全清单 v0（十八反/十九畏/妊娠禁忌）"  # 数据源显示名
    source_type = SourceType.MANUALLY_CURATED  # 人工整理类数据源（优先级最高）
    citation = "药典2020【注意】项 + 国务院令第23号 + 高校教材《中药学》（C2.6 抽查待完成）"  # 引用

    CURATED_DIR: ClassVar[str] = "curated"  # curated 清单目录名（与 raw 目录平级）

    # 解析入口：产出 Source 实体后依次解析三个安全清单文件
    def parse(self, raw_dir: Any) -> Iterator[Envelope]:
        curated = raw_dir.parent / self.CURATED_DIR  # curated 目录（raw 的同级目录）
        yield entity_envelope(self.source_entity())  # 首个产出：本源 Source 实体
        yield from self._parse_shiba_fan(curated / "shiba_fan.csv")  # 十八反清单
        yield from self._parse_shijiu_wei(curated / "shijiu_wei.csv")  # 十九畏清单
        yield from self._parse_pregnancy(curated / "pregnancy_contra.csv")  # 妊娠禁忌清单

    def _prov_curated(self) -> Any:
        return self._prov(method=ProvenanceMethod.CURATED, confidence=1.0)  # 人工核对：置信度满分

    # ---------- 十八反 ----------

    # 解析十八反清单：按 group 分组，组内 subject × object 全配对产出 CONTRAINDICATED_WITH 边
    def _parse_shiba_fan(self, path: Any) -> Iterator[Envelope]:
        if not path.exists():  # 清单缺失
            return  # 直接返回（已产出 Source 实体）
        with path.open(encoding="utf-8-sig", newline="") as f:  # utf-8-sig 兼容 Excel BOM
            rows = list(csv.DictReader(f))  # 读全部行（清单规模小）
        by_group: dict[str, dict[str, Any]] = {}  # 按歌诀组聚合容器
        for row in rows:  # 逐行归组
            group = by_group.setdefault(  # 取或建组
                row["group"], {"subject": [], "object": [], "verse": row["verse"]}
            )  # 组键 + subject/object 名单骨架与歌诀原文
            group[row["role"]].append(row["name"])  # 按角色归入 subject/object 名单
        for members in by_group.values():  # 逐组全配对
            for subject in members["subject"]:  # 反药侧
                for obj in members["object"]:  # 被反药侧
                    yield relation_envelope(  # 产出一条安全红线禁忌边
                        ContraindicatedWith(
                            start=NodeRef(label=EntityType.HERB, name=subject),  # 反药节点
                            end=NodeRef(label=EntityType.HERB, name=obj),  # 被反药节点
                            type=ContraType.EIGHTEEN_ANTAGONISMS,  # 禁忌类型：十八反
                            verse=members["verse"],  # 歌诀原文（安全审核依据）
                            provenance=self._prov_curated(),  # 人工溯源：置信度 1.0
                        )
                    )

    # ---------- 十九畏 ----------

    def _parse_shijiu_wei(self, path: Any) -> Iterator[Envelope]:
        if not path.exists():  # 清单缺失
            return  # 直接返回（已产出 Source 实体）
        with path.open(encoding="utf-8-sig", newline="") as f:  # utf-8-sig 兼容 Excel BOM
            rows = list(csv.DictReader(f))  # 读全部行（清单规模小）
        for row in rows:  # 逐对处理（每行一对畏药）
            herb_a = _NAME_NORMALIZE.get(row["herb_a"], row["herb_a"])  # 药 A：异名归一为规范名
            herb_b = _NAME_NORMALIZE.get(row["herb_b"], row["herb_b"])  # 药 B：异名归一为规范名
            if herb_a in _SKIP_HERBS or herb_b in _SKIP_HERBS:  # 含药典未收载药材（犀角）
                continue  # 不建边（图谱无该节点可挂，仅存文档）
            yield relation_envelope(  # 产出一条安全红线禁忌边
                ContraindicatedWith(
                    start=NodeRef(label=EntityType.HERB, name=herb_a),  # 畏药 A 节点
                    end=NodeRef(label=EntityType.HERB, name=herb_b),  # 畏药 B 节点
                    type=ContraType.NINETEEN_INCOMPATIBILITIES,  # 禁忌类型：十九畏
                    verse=row["verse"],  # 歌诀原文（安全审核依据）
                    provenance=self._prov_curated(),  # 人工溯源：置信度 1.0
                )
            )

    # ---------- 妊娠禁忌 ----------

    # 解析妊娠禁忌清单：先建妊娠禁忌 Category 节点，再逐行产出 PREGNANCY_CONTRA 关系
    def _parse_pregnancy(self, path: Any) -> Iterator[Envelope]:
        if not path.exists():  # 清单缺失
            return  # 直接返回（已产出 Source 实体）
        # 双轨建模（§3）：PREGNANCY_CONTRA 指向 Category('妊娠禁忌') 节点
        yield entity_envelope(
            Category(
                name=PREGNANCY_CATEGORY_NAME,  # 妊娠禁忌类目名（安全红线挂靠点）
                tree_path="safety.pregnancy",  # 安全命名空间树路径
                level=1,  # 顶级层级
                provenance=self._prov_curated(),  # 人工溯源：置信度 1.0
            )
        )
        with path.open(encoding="utf-8-sig", newline="") as f:  # utf-8-sig 兼容 Excel BOM
            rows = list(csv.DictReader(f))  # 读全部行（清单规模小）
        for row in rows:  # 逐条妊娠禁忌
            yield relation_envelope(  # 产出一条安全红线禁忌边
                PregnancyContra(
                    start=NodeRef(label=EntityType.HERB, name=row["name"]),  # 禁忌药材节点
                    end=NodeRef(label=EntityType.CATEGORY, name=PREGNANCY_CATEGORY_NAME),  # 类目
                    level=PregnancyLevel(row["level"]),  # 禁忌分级（禁用/慎用等三级）
                    provenance=self._prov_curated(),  # 人工溯源：置信度 1.0
                )
            )
