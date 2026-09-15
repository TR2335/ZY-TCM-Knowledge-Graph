# ===================================================================
# 文件：backend/tests/core/test_settings.py（core 层单测 · 配置加载）
# 用途：验证 Settings 配置类——必填项校验、默认值、嵌套环境变量覆盖（TCM_ 前缀 + __ 嵌套）
# 覆盖验收点：B5.3（缺必填时报错必须指明缺失字段名，不能含糊）
# ===================================================================

"""配置层单测（B5.3）：默认值正确、缺必填报错清晰。"""

import os  # 标准库：遍历环境变量（夹具里清除 TCM_* 用）
from collections.abc import Iterator  # 类型标注：夹具生成器返回类型

import pytest  # 测试框架：fixture/monkeypatch/raises
from pydantic import ValidationError  # Pydantic 校验失败异常（缺必填时抛出）

from tcm_kg.core.settings import Settings  # 被测对象：全局配置类

# LLM 三个必填项的合法样例：Settings 要求三项齐全才能构造成功
_REQUIRED_ENV = {
    "TCM_LLM__BASE_URL": "https://api.example.com/v1",  # 模型服务地址
    "TCM_LLM__API_KEY": "sk-test",  # 模型密钥
    "TCM_LLM__MODEL": "test-model",  # 模型名
}


@pytest.fixture(autouse=True)  # autouse=True：本文件所有用例自动生效，无需声明
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """隔离环境：清除 TCM_* 环境变量与 .env 影响，保证用例确定性。"""
    # 开发机上可能残留各种 TCM_* 变量或 .env 文件——不清除会污染断言结果
    for var in [v for v in os.environ if v.startswith("TCM_")]:  # 找出所有 TCM_ 开头的变量
        monkeypatch.delenv(var, raising=False)  # 删除（raising=False：不存在也不报错）
    yield  # 用例在此执行；结束后 monkeypatch 自动还原（测试无副作用）


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """辅助：把三个必填环境变量全部设上（合法构造的前提）。"""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)  # monkeypatch 设置的变量用例结束自动还原


def _settings() -> Settings:
    """构造 Settings 并显式忽略 .env 文件（保证用例确定性）。"""
    return Settings(_env_file=None)  # type: ignore[call-arg]  # _env_file=None：跳过 .env 加载，只看环境变量


def test_settings_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """最基本：必填齐全时能成功构造，且值正确读入。"""
    _set_required(monkeypatch)  # 先设好必填项
    settings = _settings()  # 构造配置对象
    assert settings.llm.api_key.get_secret_value() == "sk-test"  # SecretStr 要用 get_secret_value() 取明文
    assert settings.llm.model == "test-model"  # 模型名原样读入


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认值：Neo4j 本地 docker、本地 bge-m3 1024 维、并发 5。"""
    _set_required(monkeypatch)  # 只设必填项，其余全部依赖默认值
    settings = _settings()
    assert settings.neo4j.uri == "bolt://localhost:7687"  # Neo4j 默认本地地址
    assert settings.neo4j.password.get_secret_value() == "tcmkg-dev-password"  # 默认开发密码（仅限本地）
    assert settings.embedding.model_name == "BAAI/bge-m3"  # 默认嵌入模型：bge-m3
    assert settings.embedding.dim == 1024  # 默认向量维度 1024（与 bge-m3 输出一致）
    assert settings.pipeline.llm_concurrency == 5  # LLM 抽取默认并发 5
    assert settings.api.cors_origins == ["http://localhost:3000"]  # 默认允许前端 3000 端口跨域


def test_settings_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """嵌套覆盖：TCM_ 前缀 + __ 双下划线能改写深层字段（pydantic-settings 约定）。"""
    _set_required(monkeypatch)  # 必填打底
    monkeypatch.setenv("TCM_NEO4J__URI", "bolt://neo4j:7687")  # 覆盖 neo4j.uri（容器内主机名）
    monkeypatch.setenv("TCM_EMBEDDING__DEVICE", "cuda")  # 覆盖 embedding.device（GPU 推理）
    settings = _settings()
    assert settings.neo4j.uri == "bolt://neo4j:7687"  # 覆盖生效
    assert settings.embedding.device == "cuda"  # 覆盖生效


def test_settings_missing_required_reports_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """B5.3 验收：删除必填配置，报错必须指明缺失项。"""
    for key in _REQUIRED_ENV:  # 三个必填项全部删掉
        monkeypatch.delenv(key, raising=False)  # 不存在也不报错（幂等删除）
    with pytest.raises(ValidationError) as exc_info:  # 断言构造时抛校验异常
        _settings()
    message = str(exc_info.value)  # 拿到完整报错文本
    for missing in ("api_key", "base_url", "model"):  # 三个缺失字段名都必须出现在报错里
        assert missing in message  # 用户照着报错就知道要补哪个环境变量
