# ===================================================================
# 文件：DeepEavl/text_judge.py（学习脚本 · pytest 断言式评估骨架）
# 用途：展示如何把 LLM 评估变成 pytest 测试——评估不达标即测试失败（可用于 CI）
# 注意：本文件是骨架演示，good_dish/relevancy/faithful 未在此定义，
#       完整可运行版本见同目录 judge.py 的文末部分
# 运行方式：pytest DeepEavl/text_judge.py（需先补全变量定义）
# ===================================================================

from deepeval import assert_test  # DeepEval 的 pytest 桥接：断言用例必须通过指定指标
# good_dish、relevancy、faithful 从上面复制过来（或 import）


def test_宫保鸡丁():  # 以 test_ 开头：pytest 会自动发现并执行该函数
    # 断言"好答案"用例必须同时通过相关性评委和忠实度评委（任一低于阈值即测试失败）
    assert_test(good_dish, [relevancy, faithful])
