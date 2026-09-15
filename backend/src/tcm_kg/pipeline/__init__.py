# ====================================================================
# 文件：__init__.py
# 用途：管线包入口——声明 tcm_kg.pipeline 为离线数据管线子包（Phase C 容器）
# 所属层级：tcm_kg.pipeline 包入口（各阶段子模块的命名空间容器）
# 核心功能：
#   1. 标记本目录为 Python 包，声明其为离线数据管线：
#      download(C1) → parse(C3) → extract(C4) → align(C6-C8) → load(C9)
#   2. 本文件不做 re-export——各阶段函数由调用方从子模块直接导入
#      （如 from tcm_kg.pipeline.download import download_all）
#   3. 子模块地图：
#      - sources.py    数据源配置（5 源 URL/格式/解析器映射）
#      - download.py   第一阶段 C1：五源下载 + manifest 登记
#      - parse.py      第二阶段 C3：原始数据 → 统一 JSONL envelope
#      - parsers/      各数据源解析器子包（envelope 协议实现）
#      - llm.py        LLM 构建工厂（extract 抽取 / align 精判共用）
#      - embedding.py  嵌入器（bge-m3，align 召回 + 向量索引用）
#      - extract.py    C4：LLM 结构化抽取（断点续跑/并发/重试）
#      - align.py      第三阶段 C6-C8：实体对齐（召回→精判→审核队列）
#      - load.py       第四阶段 C9：入库 Neo4j + L1/L2 安全过滤
# 如何使用：
#   from tcm_kg.pipeline.download import download_all   # 各阶段从子模块导入
#   from tcm_kg.pipeline.parse import parse_cli
#   from tcm_kg.pipeline.extract import extract_cli
#   from tcm_kg.pipeline.align import align_label_pool
#   from tcm_kg.pipeline.load import load_100
# ====================================================================
"""pipeline：离线数据管线（Phase C：download/parse/extract/align/load/reindex）。"""
