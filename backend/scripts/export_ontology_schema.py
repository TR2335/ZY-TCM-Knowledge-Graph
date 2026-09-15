# ===================================================================
# 文件：backend/scripts/export_ontology_schema.py（运维脚本 · 本体 Schema 导出）
# 用途：把项目中所有中医知识图谱的实体模型和关系模型（Pydantic）导出为一份
#       JSON Schema 文件，写入 docs/generated/ontology-schema.json（A8.4 自检产物）
# 价值：① 人工审阅本体结构的快照 ② 可喂给 LLM 做信息抽取时的结构约束（todo 计划）
# 用法：cd backend && uv run python scripts/export_ontology_schema.py
# ===================================================================

"""导出本体 JSON Schema 到 docs/generated/ontology-schema.json（A8.4 自检产物）。

用法：cd backend && uv run python scripts/export_ontology_schema.py
"""

import json  # 标准库：把 Python 对象序列化为 JSON 文本
import sys  # 标准库：修改模块搜索路径（让脚本能找到 src 下的包）
from pathlib import Path  # 标准库：跨平台路径操作

# 关键一步：把 backend/src 加入模块搜索路径——脚本不在包内，
# 直接 import tcm_kg 会失败，手动指路（parents[1] = backend/，拼上 src）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tcm_kg.ontology import ONTOLOGY_VERSION, export_json_schemas  # 本体版本号 + 批量导出函数

# 项目根目录：本文件在 backend/scripts/ 下，往上两级即 tcm-kgs/
ROOT = Path(__file__).resolve().parents[2]  # backend/scripts -> tcm-kgs
# 输出文件路径：docs/generated/ontology-schema.json（generated 目录可随时重建）
OUT = ROOT / "docs" / "generated" / "ontology-schema.json"

#todo 把项目中所有中医知识图谱的实体模型和关系模型，导出为一份json Schema文件
# 用来停供给llm节后话抽取时使用（"节后话"应为"结构化"笔误——即给 LLM 抽取时提供结构约束）


def main() -> None:
    """导出全部实体/关系 JSON Schema 并写入文档目录。"""
    schemas = export_json_schemas()  # 调用本体模块：遍历全部 Pydantic 模型转成 JSON Schema 字典
    OUT.parent.mkdir(parents=True, exist_ok=True)  # 递归建目录（parents=True 连父级一起建，已存在不报错）
    # 组装导出载荷：本体版本 + 模型数量 + 全部 Schema（消费方可先校验版本再解析内容）
    payload = {
        "ontology_version": ONTOLOGY_VERSION,  # 本体语义版本（如 "1.0.0"），Schema 变更即升版
        "model_count": len(schemas),  # 导出的模型总数（实体+关系，便于抽查完整性）
        "schemas": schemas,  # 各模型的 JSON Schema 主体（模型名 → schema 字典）
    }
    # 写文件：ensure_ascii=False 保留中文可读性，indent=2 美化两空格缩进，UTF-8 编码
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 控制台回报导出结果（数量 + 版本 + 落盘路径）
    print(f"OK: {len(schemas)} 个模型（本体 v{ONTOLOGY_VERSION}）→ {OUT}")


if __name__ == "__main__":  # 直接运行本脚本时执行 main（被 import 时不动）
    main()
