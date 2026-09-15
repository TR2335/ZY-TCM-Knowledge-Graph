# ====================================================================
# 文件：sources.py
# 用途：数据源配置——定义 5 个数据源的 URL、格式、解析器映射
# 所属层级：tcm_kg.pipeline 配置层（download.py 与 parse.py 的桥梁）
# 核心功能：
#   1. 注册 5 个数据源为 SourceSpec（name/version/subdir/files）
#   2. 每源 FileSpec 列表（url + filename）
#   3. ALL_SOURCES 元组：download_all 入参
#   4. URL 均经核实（2026-08-23），版本/规模见 A1 调研报告 §3.1
# 5 个数据源：
#   - SymMap v2.0：中药-症状映射（七类描述文件）
#   - BATMAN-TCM 2.0：54832 复方（HuggingFace 镜像，CC BY-NC 4.0）
#   - nmm-data：2020 药典 616 条（GitHub，CC-BY-4.0）
#   - OpenKG 174：经典方剂君臣佐使+药典剂量（配伍金标准）
#   - GB/T 15657-2021：1369 病 + 2060 证候 + 治法（分类树骨架）
# 如何使用：
#   from tcm_kg.pipeline.sources import ALL_SOURCES
#   download_all(ALL_SOURCES, raw_dir, ...)
# ====================================================================
"""C1 冷启动数据源注册表（URL 均经核实，版本/规模见 A1 调研报告 §3.1）。

核实记录（2026-08-23）：
- SymMap v2.0：http://www.symmap.org/download/ 七类描述文件直链；
- BATMAN-TCM 2.0：官网为 SPA 无直链，采用 HuggingFace 官方镜像
  f-galkin/batman2（CC BY-NC 4.0，Insilico Medicine 整理，DOI 10.57967/hf/3314）；
- nmm-data：GitHub shennong-program/nmm-data（CC-BY-4.0，2020 药典 616 条）；
- OpenKG 174 方剂：data.openkg.cn 数据集直链（君臣佐使+药典剂量）；
- GB/T 15657-2021：中华中医药学会官网公开 PDF（204 页官方版）。

GB/T 16751.2（证候主症/次症依据）无公开电子版，C2.4 人工结构化时按纸本/教材，
C1.8 仅登记获取途径，不入 data/raw。
"""

# 总结来说，这个文件就是整个知识图谱项目的 "食材清单"，
# 定义了从哪里下载什么数据、叫什么名字、放在哪个目录。
from tcm_kg.pipeline.download import FileSpec, SourceSpec  # 数据源/文件两级规格定义

_SYMMAP_BASE = "http://www.symmap.org/static/download/V2.0"  # SymMap v2.0 下载基址


# SymMap 描述文件 URL 模板（文件名含空格需 URL 编码）
def _symmap(name: str) -> FileSpec:
    """SymMap 描述文件 URL 模板（含空格需编码）。"""
    # 拼接 URL 编码后的远程文件名（空格→%20、逗号→%2C）
    encoded = f"SymMap%20v2.0%2C%20{name}%20file.xlsx"
    # 落盘名统一规范化为 symmap_v2_<类别>.xlsx，避免中文与空格
    return FileSpec(url=f"{_SYMMAP_BASE}/{encoded}", filename=f"symmap_v2_{name}.xlsx")


_SYM_MAP = SourceSpec(
    name="symmap",  # 源标识：写入 manifest 的 source 字段
    version="v2.0",  # SymMap 版本号
    subdir="symmap",  # 原始文件存放子目录 data/raw/symmap/
    files=(  # 七类描述文件清单（下方逐行注明各自规模/用途）
        _symmap("SMHB"),  # Herb 698（含性味归经）
        _symmap("SMTS"),  # TCM symptom 2285
        _symmap("SMMS"),  # MM symptom 1148
        _symmap("SMIT"),  # Ingredient 26035（二阶段）
        _symmap("SMTT"),  # Target 20965（不入图谱，备查）
        _symmap("SMDE"),  # Disease 14086
        _symmap("SMSY"),  # Syndrome 233
    ),
)

# BATMAN-TCM 2.0 下载基址（HuggingFace 官方镜像，CC BY-NC 4.0）
_BATMAN_BASE = "https://huggingface.co/datasets/f-galkin/batman2/resolve/main/data"

# 数据源 2：BATMAN-TCM 2.0（54832 复方 + 药材；成分表 >1GB 属二阶段，推迟到需要时取）
BATMAN_TCM = SourceSpec(
    name="batman",  # 源标识
    version="2.0",  # BATMAN-TCM 版本号
    subdir="batman",  # 存放子目录 data/raw/batman/
    files=(  # 复方表 + 药材表（成分表 >1GB 属二阶段，见下）
        FileSpec(
            url=f"{_BATMAN_BASE}/batman_formulas.csv",  # 复方表直链
            filename="batman_formulas.csv",  # 落盘文件名
        ),  # 54832 复方（C1.2）
        # 药材表：复方-药材组成关系
        FileSpec(url=f"{_BATMAN_BASE}/batman_herbs.csv", filename="batman_herbs.csv"),
        # batman_ingredients.csv（>1GB）：Component 属二阶段加载，推迟到需要时再取
    ),
)

# 数据源 3：nmm-data（2020 药典 616 条，对齐锚点，CC-BY-4.0）
NMM_DATA = SourceSpec(
    name="nmm",  # 源标识
    version="2020-chp",  # 对应 2020 版《中国药典》
    subdir="nmm",  # 存放子目录 data/raw/nmm/
    files=(
        FileSpec(
            url=(  # GitHub raw 直链（CC-BY-4.0）
                "https://raw.githubusercontent.com/shennong-program/nmm-data/main/"
                "data/nmm-chinese-pharmacopoeia-2020.csv"
            ),
            filename="nmm-chinese-pharmacopoeia-2020.csv",  # 落盘文件名
        ),  # 616 条药典目录（对齐锚点，C1.3）
    ),
)

# 数据源 4：OpenKG 174 经典方剂（君臣佐使 + 药典标准化剂量，配伍金标准）
OPENKG_174 = SourceSpec(
    name="openkg",  # 源标识
    version="174-formulas",  # 数据集版本：174 首经典方剂
    subdir="openkg",  # 存放子目录 data/raw/openkg/
    files=(
        FileSpec(
            url=(  # OpenKG 数据集下载直链（data.openkg.cn）
                "http://data.openkg.cn/dataset/f9eb2263-f600-45e8-9ddb-a33596647b5d/"
                "resource/4eb4c798-1b23-44eb-a7ad-713c26b7cdeb/download/174.xlsx"
            ),
            filename="openkg_174_formulas.xlsx",  # 落盘文件名
        ),  # 174 经典方剂：君臣佐使+药典标准化剂量（配伍金标准，C1.4）
    ),
)

# 数据源 5：GB/T 15657-2021《中医病证分类与代码》国标（分类树骨架）
GBT_15657 = SourceSpec(
    name="gbt15657",  # 源标识
    version="2021",  # 国标发布年份
    subdir="gbt15657",  # 存放子目录 data/raw/gbt15657/
    files=(
        FileSpec(
            # 原址 cacm.org.cn/wp-content/uploads/2022/03/ 已 404，
            # 经 Internet Archive 固定快照获取（内容为 204 页官方 PDF）
            url=(  # Internet Archive 固定快照地址
                "https://web.archive.org/web/2024/"
                "https://www.cacm.org.cn/wp-content/uploads/2022/03/"
                "%E4%B8%AD%E5%8C%BB%E7%97%85%E8%AF%81%E5%88%86%E7%B1%BB%E4%B8%8E%E4%BB%A3%E7%A0%81.pdf"
            ),
            filename="gbt15657-2021.pdf",  # 落盘文件名
        ),  # 1369 病 + 2060 证候 + 附录 A 治法（分类树骨架，C1.5）
    ),
)

# 全部数据源元组：download_all 入参（顺序即下载顺序）
ALL_SOURCES: tuple[SourceSpec, ...] = (_SYM_MAP, BATMAN_TCM, NMM_DATA, OPENKG_174, GBT_15657)
