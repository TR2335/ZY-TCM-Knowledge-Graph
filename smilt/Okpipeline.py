# ===================================================================
# 文件：smilt/Okpipeline.py（学习脚本 · 第一次成功调用大模型）
# 用途：LangChain 调用通义千问的最小可运行示例——"我的第一家AI餐厅"
# 学习目标：理解 ① OpenAI 兼容模式如何接入国产模型 ② invoke 的基本用法
# 运行方式：先 export LANGCHAIN_API_KEY 和 OPENAI_API_KEY，再 python smilt/Okpipeline.py
# 预期输出：模型讲一个程序员冷笑话
# ===================================================================

import os  # 标准库：读写环境变量（密钥与追踪配置都从环境来）

# ========== LangSmith 链路追踪配置 ==========
# 密钥从环境变量读取（安全起见勿硬编码）：
#   LANGCHAIN_API_KEY — LangSmith 密钥；OPENAI_API_KEY — 通义千问密钥
# 1. 开启LangSmith监控（setdefault 不覆盖已有配置）
os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")  # 开启 v2 追踪：所有 LangChain 调用自动上报
# 2. LangSmith api key 必须预先 export 或配置 .env
if not os.environ.get("LANGCHAIN_API_KEY"):  # 密钥缺失检查
    raise SystemExit("缺少环境变量 LANGCHAIN_API_KEY（LangSmith 密钥），请先 export 或配置 .env")
# 项目名称，会在LangSmith页面看到
os.environ.setdefault("LANGCHAIN_PROJECT", "我的第一家AI餐厅")  # 追踪归属项目名（网页按此分组）

# ========== 模型密钥配置（通义千问 qwen‑plus） ==========
if not os.environ.get("OPENAI_API_KEY"):  # 通义千问密钥缺失检查
    raise SystemExit("缺少环境变量 OPENAI_API_KEY（通义千问密钥），请先 export 或配置 .env")
# ⚠️关键：通义千问兼容OpenAI接口，必须配置base_url
os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")  # 指向阿里云 DashScope 端点而非 OpenAI 官方

# --- 下面是做菜环节 ---
# import 放在环境配置之后：LangChain 在导入时读取环境变量，先配置才能被追踪
from langchain_openai import ChatOpenAI  # OpenAI 兼容客户端（可对接任何兼容端点）

# 聘请 qwen‑plus 厨师
chef = ChatOpenAI(model="qwen-plus")  # 实际连接通义千问 qwen-plus 模型

# 让厨师做一道菜：讲个笑话
print("厨师正在思考...")  # 友好提示（模型响应需数秒，避免用户以为卡死）
response = chef.invoke("给我讲一个关于程序员的冷笑话")  # invoke：同步调用，发送提示词并等待回复

# 打印输出结果
print(response.content)  # .content：从响应对象中取出纯文本回复
