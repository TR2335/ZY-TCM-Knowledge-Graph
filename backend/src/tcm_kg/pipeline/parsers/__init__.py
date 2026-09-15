# ===== 文件注释块 =====
# 用途：解析器包入口，导出所有解析器和解析器注册表
# 所属层级：pipeline/parsers（C3 解析归一管线包入口）
# 核心功能：聚合各数据源解析器（Batman/Curated/Nmm/OpenKG/SymMap），
#          通过 __all__ 暴露公开接口供上层管线调用。
# =====================
"""C3 解析归一管线：每源一个 parser → 统一中间 JSONL（对齐 A8 本体模型）。"""

# 导入各数据源解析器：每个解析器对应一个冷启动数据源，统一继承 SourceParser
from tcm_kg.pipeline.parsers.batman import BatmanParser  # BATMAN-TCM 2.0（方剂层规模主力）
from tcm_kg.pipeline.parsers.curated import CuratedParser  # 人工安全清单（十八反/十九畏/妊娠禁忌）
from tcm_kg.pipeline.parsers.nmm import NmmParser  # 2020 药典目录（药材名对齐锚点）
from tcm_kg.pipeline.parsers.openkg import OpenKGParser  # 174 经典方剂（配伍金标准）
from tcm_kg.pipeline.parsers.symmap import SymMapParser  # SymMap 症状映射数据库

# 解析器注册表：声明包对外公开的解析器类清单
__all__ = [
    "BatmanParser",  # BATMAN-TCM 解析器
    "CuratedParser",  # 人工安全清单解析器
    "NmmParser",  # 药典目录解析器
    "OpenKGParser",  # OpenKG 方剂解析器
    "SymMapParser",  # SymMap 解析器
]
