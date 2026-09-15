# ===================================================================
# 文件：backend/tests/conftest.py（pytest 全局夹具）
# 用途：定义跨测试文件共享的 fixture——conftest.py 是 pytest 约定文件，
#       其中定义的 fixture 无需 import 即可在任意测试文件中使用
# 核心夹具：prov——标准溯源四元组（多处测试需要给实体/关系挂出处时复用）
# ===================================================================

"""共享测试夹具。"""

import pytest  # 测试框架：提供 fixture 装饰器等能力

from tcm_kg.ontology import Provenance, ProvenanceMethod  # 溯源模型 + 获取方式枚举


@pytest.fixture  # 注册为 fixture：测试函数声明参数名 prov 即自动注入
def prov() -> Provenance:
    """标准出处：药典数据库导入，置信度 0.95。"""
    return Provenance(  # 返回一个可直接使用的溯源四元组
        source_id="src-yp2020",  # 指向 2020 药典数据源（Source 节点 ID）
        confidence=0.95,  # 置信度 0.95（药典属权威来源，给高分）
        method=ProvenanceMethod.DB_IMPORT,  # 获取方式：数据库导入（区别于 LLM 抽取/人工整理）
    )
