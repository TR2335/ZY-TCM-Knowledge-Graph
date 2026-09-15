# ===================================================================
# 文件：backend/scripts/inspect_align.py（运维脚本 · 实体对齐质量抽查）
# 用途：一次性开发脚本——抽查 C6 对齐阶段产出的"合并候选"质量，
#       人工肉眼判断哪些药名对（如异名→规范名）合并得对、哪些合并错了
# 数据源：data/aligned-herb-test2/merge_candidates.jsonl（对齐阶段输出的候选对清单）
# 用法：cd backend/scripts && python inspect_align.py（需先跑过 tcm align）
# ===================================================================

"""抽查合并候选质量（一次性开发脚本）。"""

import json  # 标准库：逐行解析 JSONL 文件
import random  # 标准库：随机抽样（固定种子保证可复现）
from pathlib import Path  # 标准库：文件路径操作

# 待抽查文件：药材实体对齐的合并候选清单（相对路径，从 backend/scripts 运行）
p = Path(r"..\data\aligned-herb-test2\merge_candidates.jsonl")
# JSONL 格式：每行一个独立 JSON 对象——逐行读取解析成字典列表
rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
# 第一轮抽查：总体概况 + 随机 15 条（先建立整体印象）
print(f"合并候选 {len(rows)} 对，抽样 15 条：")
random.seed(42)  # 固定随机种子：每次运行抽到同样的样本，问题可复现、结论可对比
for d in random.sample(rows, 15):  # 不放回抽 15 条候选对
    # 打印格式：A ←→ B（两药名）+ 相似度 sim + 置信度 conf + 判定理由前 32 字符
    print(f"  {d['a']} ←→ {d['b']}  sim={d['sim']} conf={d['confidence']} [{d['reason'][:32]}]")

# 第二轮专项抽查：只看"语义合并"——相似度 < 0.99 意味着不是完全相同的字符串，
# 是靠语义判断出来的合并（如"朴硝"→"芒硝"），这部分最容易合错，值得单独盯
semantic = [d for d in rows if d["sim"] < 0.99]
print(f"\n语义合并（非字符等价）{len(semantic)} 对，抽样 12 条：")
for d in random.sample(semantic, min(12, len(semantic))):  # 最多抽 12 条（不足 12 则全抽，防越界）
    # 只打名称对和相似度（这轮关注"这两个名字真的是同一味药吗"）
    print(f"  {d['a']} ←→ {d['b']}  sim={d['sim']}")
