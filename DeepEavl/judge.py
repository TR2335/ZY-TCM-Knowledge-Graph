# ===================================================================
# 文件：DeepEavl/judge.py（学习脚本 · DeepEval LLM-as-a-Judge 评估入门）
# 用途：用 DeepEval 框架 + 通义千问当裁判，自动评估两个回答的好坏——
#       同一个问题"宫保鸡丁多少钱"，一个好答案（照菜单说）vs 一个坏答案（瞎编+废话）
# 学习目标：理解 ① LLMTestCase 测试用例结构 ② 两大核心指标：
#           AnswerRelevancyMetric（相关性：答非所问吗）与 FaithfulnessMetric（忠实度：编造了吗）
# 运行方式：先 export QWEN_API_KEY，再 pytest DeepEavl/judge.py（或直接 python 运行看评估）
# 指标原理（文末 todo 注释有详细拆解）：
#   忠实度 = 把答案拆成一条条事实，逐条与检索资料比对，对的上的/总条数
#   相关性 = 根据答案反推题目，与真实问题对比，猜偏了就扣分
# ===================================================================

import os  # 标准库：读取环境变量（裁判模型的密钥）

# ========== 通义千问密钥：从环境变量读取（安全起见勿硬编码） ==========
# 优先读 QWEN_API_KEY，其次复用 OPENAI_API_KEY（同为 DashScope 系密钥）
# or 链式取值：第一个非空生效，两处都没配则为 None
QWEN_KEY = os.environ.get("QWEN_API_KEY") or os.environ.get("OPENAI_API_KEY")
if not QWEN_KEY:  # 密钥缺失：明确提示后退出，避免后续报晦涩的 401 错误
    raise SystemExit("缺少环境变量 QWEN_API_KEY（通义千问密钥），请先 export 或配置 .env")

# ========== 第 1 步：聘请评委（qwen-plus 当裁判） ==========
from deepeval.models import OpenAIModel  # DeepEval 的 OpenAI 兼容模型封装（可接任何兼容端点）

# 裁判模型：评分时不看被评模型是谁，只按指标标准打分
judge = OpenAIModel(
    model="qwen-plus",  # 用通义千问 qwen-plus 当裁判（性价比高）
    api_key=QWEN_KEY,  # 裁判自己的密钥（显式传入，不依赖环境加载时机）
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # 通义的兼容地址
)

# ========== 第 2 步：准备两道菜 ==========
from deepeval.test_case import LLMTestCase  # 测试用例类：一次问答的完整快照

# 检索到的"资料"——模拟 RAG 场景中知识库返回的上下文（评估忠实度的对照物）
menu = ["菜单价格：宫保鸡丁 32 元，麻婆豆腐 22 元，可乐 8 元。"]

# 好答案用例：老实按菜单回答
good_dish = LLMTestCase(
    input="宫保鸡丁多少钱？",  # 用户提问
    actual_output="宫保鸡丁是 32 元。",          # ✅ 老实回答：与菜单一致，无废话
    retrieval_context=menu,  # 提供检索资料：让忠实度评委有对照依据
)

# 坏答案用例：瞎编价格 + 推销无关菜品
bad_dish = LLMTestCase(
    input="宫保鸡丁多少钱？",  # 同样的问题
    actual_output="宫保鸡丁 99 元！另外我们臭豆腐也是招牌，一定要点。",  # ❌ 瞎编价格+废话
    retrieval_context=menu,  # 同样的菜单（评估时能查出它没照着说）
)

# ========== 第 3 步：请两位评委，及格线 0.5 ==========
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric  # 两大核心指标

relevancy = AnswerRelevancyMetric(threshold=0.5, model=judge)  # 评委A：回答切题吗？（分数<0.5 判失败）
faithful  = FaithfulnessMetric(threshold=0.5, model=judge)    # 评委B：有没有照着菜单说？（防幻觉核心指标）

# ========== 第 4 步：开评！ ==========
from deepeval import evaluate  # 批量评估入口

# 对两个用例分别跑两个指标：控制台会打印每个用例的得分与通过/失败
evaluate(test_cases=[good_dish, bad_dish], metrics=[relevancy, faithful])

#todo 忠实度 计算 类似 NIL模型 拆分原句事实 逐条和事实对比 对的上的/总体的 = 忠实度
#todo 相关性 根据答案 反向猜题目 与真问题对比 猜错扣分

# ========== 进阶：把评估变成 pytest 断言（CI 可用） ==========
from deepeval import assert_test  # pytest 断言桥：评估失败 = 测试失败

# good_dish、relevancy、faithful 从上面复制过来（或 import）
def test_宫保鸡丁():  # pytest 用例：跑 pytest 时自动执行，评分不达标直接红
    assert_test(good_dish, [relevancy, faithful])  # 断言好答案必须同时通过两个评委
