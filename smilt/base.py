# ===================================================================
# 文件：smilt/base.py（学习脚本 · LangSmith 连通性自检）
# 用途：验证 LangSmith 密钥是否有效、网络是否能连通——学习 LangSmith 的第一步
# 运行方式：先 export LANGCHAIN_API_KEY=你的密钥，再 python smilt/base.py
# 预期输出：看到 "✅ 恭喜！保安开门了" 即代表连通成功
# ===================================================================

import os  # 标准库：读取环境变量（密钥从环境来，不写死在代码里）

from langsmith import Client  # LangSmith 官方 SDK 客户端，用于连接追踪平台
from langsmith.utils import LangSmithError  # LangSmith 专属异常类（密钥错误/网络问题时抛出）

# ⚠️ 密钥从环境变量读取（安全起见勿硬编码）：预先 export LANGCHAIN_API_KEY
MY_KEY = os.environ.get("LANGCHAIN_API_KEY")  # 读环境变量；没有则返回 None
if not MY_KEY:  # 密钥为空说明用户忘了配置
    # 直接退出并给出可操作的提示（比半路报晦涩错误友好得多）
    raise SystemExit("缺少环境变量 LANGCHAIN_API_KEY（LangSmith 密钥），请先 export 或配置 .env")

# 友好的运行提示（让用户知道脚本在做什么）
print("🚀 正在拿着钥匙去敲 LangSmith 服务器的大门...")

try:
    # 直接用参数传 Key，不走 os.environ，最稳！
    # 创建客户端：显式传入 api_key，避免依赖库去猜环境变量加载时机
    client = Client(api_key=MY_KEY)
    # 调用最轻量的接口（只拉 1 个项目）来验证密钥+网络是否正常
    projects = list(client.list_projects(limit=1))

    # 能走到这里说明密钥有效、网络通畅
    print("✅ 恭喜！保安开门了，连通性 100% 没问题！")
    # 给学习者的下一步指引：后续代码同样要显式传 Key
    print("👉 接下来，你应该在讲笑话的代码里，使用 `Client` 显式传入 Key，或者确保你的环境变量真正加载了。")

except LangSmithError as e:
    # LangSmith 专属错误：密钥无效、项目不存在等（服务器拒绝了请求）
    print(f"❌ 连接失败！服务器原话：{e}")
except Exception as e:
    # 兜底捕获其他意外错误：网络超时、DNS 解析失败等
    print(f"⚠️ 意外错误: {e}")
