# ===================================================================
# 文件：smilt/Nopipeline.py（学习脚本 · LangSmith 追踪实验）
# 用途：演示 LangSmith 的 @traceable 装饰器如何记录"正确与报错"的调用链——
#       包含 5 个案例：2 个正常回答 + 3 个不同类型的报错（除零/模型名错/JSON 解析失败）
# 学习目标：理解 ① @traceable 装饰器自动上报 ② 报错也会被完整记录到 LangSmith 网页
# 运行方式：先 export LANGCHAIN_API_KEY 和 OPENAI_API_KEY，再 python smilt/Nopipeline.py
# ===================================================================

import os    # 标准库：读写环境变量（配置 LangSmith 与模型密钥）
import time  # 标准库：计时（统计每个案例的耗时）
import json  # 标准库：JSON 解析（案例 3 故意用它制造解析报错）

# ========== 配置区：密钥从环境变量读取（安全起见勿硬编码） ==========
# 运行前请设置以下环境变量（可 export 或写入 .env 后加载）：
#   LANGCHAIN_API_KEY — LangSmith 追踪密钥
#   OPENAI_API_KEY    — 通义千问（DashScope）API Key
# 非敏感默认项用 setdefault 写入，不覆盖已有配置
# setdefault：环境变量里已有值就不动，没有才写入默认值（幂等，可反复运行）
os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")  # 开启 LangSmith v2 链路追踪
os.environ.setdefault("LANGCHAIN_PROJECT", "NoPipeline")  # 追踪数据归属的项目名（网页上按此分组）
os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")  # 通义千问的 OpenAI 兼容端点

# 密钥缺失时给出明确提示并退出，避免半路报晦涩错误
if not os.environ.get("LANGCHAIN_API_KEY"):  # 检查 LangSmith 密钥是否存在
    raise SystemExit("缺少环境变量 LANGCHAIN_API_KEY（LangSmith 密钥），请先 export 或配置 .env")
if not os.environ.get("OPENAI_API_KEY"):  # 检查通义千问密钥是否存在
    raise SystemExit("缺少环境变量 OPENAI_API_KEY（通义千问密钥），请先 export 或配置 .env")

# 注意：import 放在配置之后是刻意为之——LangChain 读取环境变量的时机在 import 时，
# 先配置再导入才能保证追踪生效（这也是 LangChain 生态的常见坑）
from langsmith import traceable  # @traceable 装饰器：被装饰函数的每次调用自动上报 LangSmith
from langchain_openai import ChatOpenAI  # OpenAI 兼容客户端：配好 base_url 后即可调用通义千问

# 创建"厨师"模型实例：实际连接的是通义千问 qwen-plus（OpenAI 兼容模式）
chef = ChatOpenAI(model="qwen-plus")

# ========== ✅ 正确案例 1：模型正常回答 ==========
@traceable(name="正确-算数题")  # 上报到 LangSmith 时显示的自定义名称（网页里好认）
def case_ok_math():
    resp = chef.invoke("1+1等于几？只回答一个数字")  # invoke：同步调用模型并等待回答
    return resp.content  # .content 取出模型回复的纯文本


# ========== ✅ 正确案例 2：模型正常回答 ==========
@traceable(name="正确-讲笑话")
def case_ok_joke():
    resp = chef.invoke("用一句话讲一个程序员笑话")  # 换个提示词，再测一次正常链路
    return resp.content  # 返回笑话文本


# ========== ❌ 错误案例 1：代码自己崩溃（除零） ==========
@traceable(name="错误-除以零崩溃")
def case_err_div_zero():
    return 1 / 0  # 经典除零错误：Python 抛 ZeroDivisionError，LangSmith 会记录报错现场


# ========== ❌ 错误案例 2：模型名字写错，API 报错 ==========
@traceable(name="错误-模型不存在")
def case_err_bad_model():
    # 故意用一个不存在的模型名——API 服务端会拒绝并返回错误
    bad_chef = ChatOpenAI(model="qwen-不存在的模型-999")
    return bad_chef.invoke("你好").content  # invoke 时才真正发起请求，此处抛 API 错误


# ========== ❌ 错误案例 3：解析数据崩溃 ==========
@traceable(name="错误-JSON解析失败")
def case_err_bad_json():
    # 把一段非法 JSON 文本喂给解析器——抛 json.JSONDecodeError
    # 模拟真实场景：模型返回的内容不符合预期的 JSON 格式
    return json.loads("这不是json!!!")


# ========== 主程序：逐个运行，报错可见 ==========
if __name__ == "__main__":  # 直接运行本文件时才执行（被 import 时不执行）
    # 案例清单：(显示名, 函数对象) —— 函数不调用，只登记，稍后统一循环执行
    cases = [
        ("✅ 正确1", case_ok_math),       # 算数题：预期成功
        ("✅ 正确2", case_ok_joke),       # 讲笑话：预期成功
        ("❌ 错误1", case_err_div_zero),   # 除零：预期崩溃
        ("❌ 错误2", case_err_bad_model),  # 模型名错：预期 API 报错
        ("❌ 错误3", case_err_bad_json),   # JSON 解析：预期崩溃
    ]

    print("=" * 60)  # 打印分隔线，让控制台输出更清晰
    for name, fn in cases:  # 逐个执行案例
        start = time.time()  # 记录开始时间（秒级浮点数）
        try:
            result = fn()  # 调用案例函数：成功则拿到返回值
            # 成功输出：耗时保留 1 位小数，结果截取前 40 字符防止刷屏
            print(f"{name} | 成功 | 耗时 {time.time()-start:.1f}s | 结果: {str(result)[:40]}")
        except Exception as e:  # 捕获一切异常：保证某个案例失败不影响后续案例运行
            # 失败输出：只显示异常类名（如 ZeroDivisionError），简洁定位问题类型
            print(f"{name} | 失败 | 耗时 {time.time()-start:.1f}s | 错误类型: {type(e).__name__}")
            # 第二行补充错误详情，截取前 100 字符
            print(f"          错误信息: {str(e)[:100]}")
    print("=" * 60)  # 结束分隔线
    # 提示用户去 LangSmith 网页查看完整追踪（网页端能看到每个案例的输入输出/报错堆栈）
    print("🎬 全部运行完毕！等 10 秒后刷新 LangSmith 网页，打开项目【报错可见演示】")
