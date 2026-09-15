"""本体枚举定义 —— 与 docs/ontology-design.md §4 零偏差。

约定：标识符英文、枚举值用中文规范术语；任何枚举值变更必须先改设计文档
（schema 变更流程见 ontology-design.md §6）。SourceType / ProvenanceMethod
分别来自设计文档 §2.11 与 §5。
"""

# ============================================================
# 文件：enums.py
# 用途：本体枚举定义层
# 所属层级：tcm_kg.ontology（本体层 —— 单一事实源代码化）
# 核心功能：定义 11 类核心枚举（药性 / 药味 / 归经 / 毒性 / 安全级等），
#          是整个中医知识图谱的术语基础与规范约束源头
# 如何使用：被 entities.py / relations.py / provenance.py 等本体模块导入；
#          加载器、质检、API 层通过 __init__.py 统一导出引用；
#          任何枚举值变更必须先改设计文档（§6）
# ============================================================

# 规范化术语  术语词典 约束保证术语一致
# 任何地方需要引用中医规范术语（如药性、药味、归经、毒性等级等），
# 都必须从这里导入，确保全系统术语统一。
from enum import StrEnum # 用于定义枚举值为字符串类型

# 药性枚举：寒热温凉平八级，描述中药性能的核心维度（§4）
class Nature(StrEnum):
    """药性（§4：寒热温凉平八级）。"""

    COLD = "寒"  # 寒：清热泻火，如黄连
    SLIGHTLY_COLD = "微寒"  # 微寒：寒性稍轻，如桑叶
    COOL = "凉"  # 凉：凉性次于寒，如薄荷
    NEUTRAL = "平"  # 平：寒热偏性不显，如山药
    WARM = "温"  # 温：温里散寒，如当归
    SLIGHTLY_WARM = "微温"  # 微温：温性稍轻，如太子参
    HOT = "热"  # 热：温里力强，如附子
    EXTREMELY_HOT = "大热"  # 大热：热性最峻，如干姜


class Flavor(StrEnum):
    """药味（§4：酸苦甘辛咸涩淡）。"""

    SOUR = "酸"  # 酸：收敛固涩，如山茱萸
    BITTER = "苦"  # 苦：泄降燥湿，如黄连
    SWEET = "甘"  # 甘：补益和中，如甘草
    PUNGENT = "辛"  # 辛：发散行气，如麻黄
    SALTY = "咸"  # 咸：软坚泻下，如芒硝
    ASTRINGENT = "涩"  # 涩：收敛固脱，如五倍子
    BLAND = "淡"  # 淡：渗利水湿，如茯苓


class Meridian(StrEnum):
    """归经（§4：十二经）。"""

    LUNG = "肺"  # 手太阴肺经
    LARGE_INTESTINE = "大肠"  # 手阳明大肠经
    STOMACH = "胃"  # 足阳明胃经
    SPLEEN = "脾"  # 足太阴脾经
    HEART = "心"  # 手少阴心经
    SMALL_INTESTINE = "小肠"  # 手太阳小肠经
    BLADDER = "膀胱"  # 足太阳膀胱经
    KIDNEY = "肾"  # 足少阴肾经
    PERICARDIUM = "心包"  # 手厥阴心包经
    TRIPLE_ENERGIZER = "三焦"  # 手少阳三焦经
    GALLBLADDER = "胆"  # 足少阳胆经
    LIVER = "肝"  # 足厥阴肝经


class Toxicity(StrEnum):
    """毒性分级（§4：无毒/小毒/有毒/大毒）。

    rank 为毒性严重度序（供 L2 方剂分级与 L3 剂量隐藏判定使用，§7）；
    requires_dose_masking 直接实现 §7.4 剂量隐藏矩阵的药材判定条件。
    """

    NONE = "无毒"  # 常规剂量下安全
    SLIGHT = "小毒"  # 毒性轻微，慎用可控
    TOXIC = "有毒"  # 需炮制控量，触发剂量隐藏
    HIGHLY_TOXIC = "大毒"  # 毒性峻烈，严格管控

    @property
    def rank(self) -> int:
        """严重度序：无毒=0 < 小毒=1 < 有毒=2 < 大毒=3。"""
        return {"无毒": 0, "小毒": 1, "有毒": 2, "大毒": 3}[self.value]  # 值→严重度序数映射

    @property
    def requires_dose_masking(self) -> bool:
        """剂量隐藏判定（§7.4）：毒性 ∈ {有毒, 大毒} → True。"""
        return self.rank >= Toxicity.TOXIC.rank  # 达"有毒"级即需掩码


class SafetyLevel(StrEnum):
    """方剂安全分级（§4 / §7.1 L2）。

    注意：不存在 high_risk——含毒性管理品种生品的方剂在 L1 数据层即被过滤，
    根本不入库（ADR-013）。
    """

    SAFE = "safe"  # 安全：仅含小毒/无毒药材
    CAUTION = "caution"  # 谨慎：含"有毒"级药材，用法对外掩码


# 妊娠禁忌分级枚举：禁用/忌用/慎用/无标记，依据药典"注意"项（§4）
class PregnancyLevel(StrEnum):
    """妊娠禁忌分级（§4：药典"注意"项口径）。"""

    PROHIBITED = "禁用"  # 妊娠期绝对禁用
    AVOID = "忌用"  # 妊娠期忌用，能避则避
    CAUTION = "慎用"  # 妊娠期慎用，权衡后用
    UNMARKED = "无标记"  # 药典"注意"项无妊娠标记


# 方剂配伍角色枚举：君臣佐使，方剂组成中各药材的职能定位（§4）
class FormulaRole(StrEnum):
    """方剂配伍角色（§4：君臣佐使）。"""

    MONARCH = "君"  # 君药：主攻主证
    MINISTER = "臣"  # 臣药：辅君增效
    ASSISTANT = "佐"  # 佐药：佐制反佐/治兼证
    ENVOY = "使"  # 使药：引经调和诸药


class FormulaType(StrEnum):
    """方剂类型（§4；中成药以属性建模而非独立实体，ADR-011）。"""

    CLASSIC = "经方"  # 经典古方（伤寒论等）
    LATER = "时方"  # 后世时方（温病等）
    PATENT = "中成药"  # 成药（ADR-011 属性建模）


class DosageForm(StrEnum):
    """剂型（§4）。"""

    PILL = "丸"  # 丸剂：缓效持久
    POWDER = "散"  # 散剂：起效较快
    PASTE = "膏"  # 膏剂：滋补调理
    ELIXIR = "丹"  # 丹剂
    GRANULE = "颗粒"  # 颗粒剂：现代常用
    ORAL_LIQUID = "口服液"  # 口服液：液体剂型
    TABLET = "片"  # 片剂
    CAPSULE = "胶囊"  # 胶囊剂


# 剂量单位枚举：g/mg/ml，古典单位按换算表折算为 g 后入库（§4）
class DoseUnit(StrEnum):
    """剂量单位（§4：古典单位按换算表折算为 g 后入库，原文存 raw_dose）。"""

    GRAM = "g"  # 克：入库统一单位
    MILLIGRAM = "mg"  # 毫克：微量成分
    MILLILITER = "ml"  # 毫升：液体药剂


# 七情配伍类型枚举：相须/相使/相畏/相杀，描述药对增效或制约关系（§4）
class SynonymType(StrEnum):
    """七情配伍类型（§4）。

    相反（十八反）走 CONTRAINDICATED_WITH；相恶暂不建模。
    """

    MUTUAL_REINFORCEMENT = "相须"  # 相须：同类协同增效
    MUTUAL_ASSISTANCE = "相使"  # 相使：异类辅佐增效
    MUTUAL_RESTRAINT = "相畏"  # 相畏：受彼制约减毒
    MUTUAL_DETOXICATION = "相杀"  # 相杀：制彼毒性


class ContraType(StrEnum):
    """配伍禁忌类型（§4）。"""

    EIGHTEEN_ANTAGONISMS = "十八反"  # 十八反歌诀所载药对禁忌
    NINETEEN_INCOMPATIBILITIES = "十九畏"  # 十九畏歌诀所载药对禁忌


class PatternCategory(StrEnum):
    """证候分类（§4：8 类，排除韩医四象）。"""

    EIGHT_PRINCIPLES = "八纲证"  # 阴阳表里寒热虚实
    ETIOLOGY = "病因证"  # 六淫痰饮瘀血等
    QI_BLOOD_FLUID = "气血津液证"  # 气血津液亏瘀失常
    ZANG_FU = "脏腑证"  # 脏腑功能失调
    MERIDIAN = "经络证"  # 经络循行部位病变
    SIX_CHANNEL = "六经证"  # 伤寒六经辨证
    TRIPLE_ENERGIZER = "三焦证"  # 温病三焦辨证
    WEI_QI_YING_XUE = "卫气营血证"  # 温病卫气营血辨证


class SourceType(StrEnum):
    """知识来源类型（§2.11）。"""

    CLASSIC_TEXT = "典籍"  # 中医经典古籍
    NATIONAL_STANDARD = "国家标准"  # GB/T 等国标文件
    PHARMACOPOEIA = "药典"  # 中国药典（2020）
    DATABASE = "数据库"  # 结构化数据库
    MANUALLY_CURATED = "人工整理"  # 专家核对整理


# 知识获取方式枚举：curated/db_import/llm_extract/manual_review，决定置信度（§5）
class ProvenanceMethod(StrEnum):
    """知识获取方式（§5）。

    置信度约定：curated=1.0；db_import=0.95；llm_extract=模型自评（默认 0.7）；
    manual_review 永远最高优先（多源冲突展示按 confidence 排序）。
    """

    CURATED = "curated"  # 人工精校，置信度 1.0
    DB_IMPORT = "db_import"  # 库导入，置信度 0.95
    LLM_EXTRACT = "llm_extract"  # LLM 抽取，模型自评
    MANUAL_REVIEW = "manual_review"  # 人工复核，最高优先
