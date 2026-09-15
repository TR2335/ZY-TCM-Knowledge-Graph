# ====================================================================
# 文件：parse.py
# 用途：数据管线第二阶段（C3）——将原始数据解析为统一的 JSONL envelope 格式
# 所属层级：tcm_kg.pipeline 解析层（离线管线 Phase C 第 2 步）
# 核心功能：
#   1. 编排各数据源 parser，产出 data/parsed/<source>.jsonl
#   2. 统一 envelope 格式：kind=entity|relation|quarantine + label/type + record
#   3. 隔离清单 <source>.quarantine.jsonl（解析失败/校验不过的行）
#   4. ParseStats 统计：实体数/关系数/隔离数/校验率（C3.6 验收口径）
#   5. 人类可读统计报告 format_report
# 如何使用：
#   from tcm_kg.pipeline.parse import parse_cli
#   stats = parse_cli(["all"], raw_dir, parsed_dir)
#   # 或 CLI：python -m tcm_kg.pipeline parse --sources all
# ====================================================================
"""C3 编排：运行 parser → data/parsed/<source>.jsonl + 隔离清单 + 统计。"""

# parse.py 就是项目的数据"解析工"——从 data/raw 目录读取原始数据，

import json  # envelope 序列化为 JSONL 行
from dataclasses import dataclass, field  # ParseStats 统计数据类
from pathlib import Path  # raw/parsed 目录路径处理

from tcm_kg.core.logging import get_logger  # 项目统一结构化日志
from tcm_kg.pipeline.parsers.base import SourceParser  # 解析器基类（envelope 协议）
from tcm_kg.pipeline.parsers.batman import BatmanParser  # BATMAN-TCM 2.0 解析器
from tcm_kg.pipeline.parsers.curated import CuratedParser  # 人工策划数据解析器
from tcm_kg.pipeline.parsers.gbt import Gbt15657Parser  # GB/T 15657 国标 PDF 解析器
from tcm_kg.pipeline.parsers.nmm import NmmParser  # 药典 nmm-data 解析器
from tcm_kg.pipeline.parsers.openkg import OpenKGParser  # OpenKG 174 方剂解析器
from tcm_kg.pipeline.parsers.symmap import SymMapParser  # SymMap v2.0 解析器

logger = get_logger(__name__)  # 模块级 logger

# 数据源 key → 解析器类映射（run_all 按此选择 parser）
PARSERS: dict[str, type[SourceParser]] = {  # key 对应 sources.py 中的 source 标识
    "symmap": SymMapParser,  # 中药-症状映射（七类描述文件）
    "nmm": NmmParser,  # 2020 药典 616 条（对齐锚点）
    "openkg": OpenKGParser,  # 174 经典方剂（君臣佐使金标准）
    "curated": CuratedParser,  # 人工策划/校订数据
    "batman": BatmanParser,  # 54832 复方 + 药材
    "gbt15657": Gbt15657Parser,  # 病证分类与代码国标（分类树骨架）
}


@dataclass
class ParseStats:
    """单源解析统计（C3.6 验收口径）。"""

    source: str  # 数据源标识（如 symmap）
    entities: dict[str, int] = field(default_factory=dict)  # 实体计数：label → 数量
    relations: dict[str, int] = field(default_factory=dict)  # 关系计数：type → 数量
    quarantined: int = 0  # 被隔离条目数（解析失败/校验不过）

    # 有效条目总数（实体 + 关系）
    @property
    def total_valid(self) -> int:
        return sum(self.entities.values()) + sum(self.relations.values())  # 实体+关系合计

    # 校验通过率 = 有效 / (有效 + 隔离)；空集记 1.0
    @property
    def validation_rate(self) -> float:
        total = self.total_valid + self.quarantined  # 参与校验的总条目数
        return self.total_valid / total if total else 1.0  # 空数据集按 100% 记


def run_parser(parser_cls: type[SourceParser], raw_dir: Path, out_dir: Path) -> ParseStats:
    """运行单个 parser：产出 <source_id>.jsonl 与 .quarantine.jsonl。"""
    parser = parser_cls()  # 实例化解析器
    stats = ParseStats(source=parser.source_id)  # 初始化该源统计
    out_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    main_path = out_dir / f"{parser.source_id}.jsonl"  # 主输出文件（有效条目）
    quarantine_path = out_dir / f"{parser.source_id}.quarantine.jsonl"  # 隔离清单文件

    with (  # 同时打开主文件与隔离文件两个句柄
        main_path.open("w", encoding="utf-8") as main_fh,  # 主文件句柄
        quarantine_path.open("w", encoding="utf-8") as quarantine_fh,  # 隔离文件句柄
    ):
        for envelope in parser.parse(raw_dir):  # 流式逐条产出 envelope
            if envelope["kind"] == "quarantine":  # 隔离条目（解析失败/校验不过）
                stats.quarantined += 1  # 隔离计数累加
                # default=str 兜底不可 JSON 序列化的字段（保留现场便于排查）
                quarantine_fh.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
                continue  # 不进入主文件
            main_fh.write(json.dumps(envelope, ensure_ascii=False) + "\n")  # 写入主文件
            if envelope["kind"] == "entity":  # 实体条目
                label = envelope["label"]  # Neo4j 节点标签
                stats.entities[label] = stats.entities.get(label, 0) + 1  # 按 label 计数
            else:  # 关系条目
                rel_type = envelope["type"]  # 关系类型
                stats.relations[rel_type] = stats.relations.get(rel_type, 0) + 1  # 按 type 计数
    logger.info(  # 输出单源解析完成日志
        "parse_done",
        source=parser.source_id,
        entities=stats.entities,
        relations=stats.relations,
        quarantined=stats.quarantined,
        rate=f"{stats.validation_rate:.2%}",  # 校验通过率（C3.6 验收口径）
    )
    return stats  # 返回单源统计


# 按数据源 key 批量运行 parser（"all" = 全部；未知 key 抛错）
def run_all(sources: list[str], raw_dir: Path, out_dir: Path) -> list[ParseStats]:
    """按 key 运行若干 parser（'all' = 全部）。"""
    keys = list(PARSERS) if "all" in sources else sources  # "all" 展开为全部注册源
    unknown = [k for k in keys if k not in PARSERS]  # 收集未注册的源 key
    if unknown:  # 存在未知数据源
        raise ValueError(f"未知数据源：{unknown}（可选：{list(PARSERS)}）")  # 报错并列出可选项
    return [run_parser(PARSERS[key], raw_dir, out_dir) for key in keys]  # 逐源运行解析


def format_report(stats_list: list[ParseStats]) -> str:
    """人类可读统计报告（含 C3 验收的校验率）。"""
    lines: list[str] = []  # 报告行收集器
    for stats in stats_list:  # 每个源生成一行摘要
        # 实体统计串，如 "Herb 698, Syndrome 233"
        entity_text = ", ".join(f"{label} {count}" for label, count in stats.entities.items())
        # 关系统计串，如 "TREATS 1234"
        relation_text = ", ".join(f"{t} {c}" for t, c in stats.relations.items())
        lines.append(  # 汇总单源报告行（源名+实体+关系+隔离+校验率）
            f"[{stats.source}] 实体: {{{entity_text}}} 关系: {{{relation_text}}} "
            f"隔离: {stats.quarantined} 校验率: {stats.validation_rate:.2%}"  # 隔离数与校验率
        )
    return "\n".join(lines)  # 拼接为多行报告文本


# CLI 入口：解析数据源并返回统计（打印由 CLI 层负责）
def parse_cli(sources: list[str], raw_dir: Path, out_dir: Path) -> list[ParseStats]:
    """CLI 入口：解析并返回统计（打印由 CLI 层负责）。"""
    return run_all(sources, raw_dir, out_dir)  # 直接委托 run_all 执行
