# ===================================================================
# 文件：settings.py
# 用途：全局配置管理，从环境变量读取 Neo4j 连接、LLM 配置等
# 所属层级：core 横切层（B5 · 配置基础设施，被各层引用，自身零业务依赖）
# 核心功能：
#   - 基于 pydantic-settings 实现类型安全的嵌套分组配置（llm/neo4j/embedding/
#     api/observability/pipeline）
#   - 环境变量前缀 TCM_ + 嵌套分隔 __（如 TCM_LLM__API_KEY）
#   - LLM 三项必填，缺失时启动即报 ValidationError 并指明字段路径
#   - get_settings() 进程级缓存（lru_cache）
# 如何使用：
#   from tcm_kg.core.settings import get_settings; cfg = get_settings()
#   或按分组取：Neo4jSettings() / LLMSettings()；测试请直接构造或清理缓存。
# ===================================================================

"""应用配置（B5）：pydantic-settings 嵌套分组。

环境变量约定：前缀 TCM_ + 嵌套分隔 __，例如
TCM_LLM__API_KEY、TCM_NEO4J__URI、TCM_EMBEDDING__DEVICE。
必填项（LLM 三项）缺失时启动即报 ValidationError 并指明字段路径（B5.3 验收）。
"""

from functools import lru_cache  # 函数缓存工具，用于 get_settings 单例

from pydantic import BaseModel, ConfigDict, Field, SecretStr  # 基类、字段与密文类型
from pydantic_settings import BaseSettings, SettingsConfigDict  # 环境变量配置加载基类


# LLM 服务配置：OpenAI 兼容接口（base_url/api_key/model 三项必填）
class LLMSettings(BaseModel):
    """LLM 服务（OpenAI 兼容接口）。"""

    model_config = ConfigDict(extra="ignore")  # 忽略未声明的多余字段

    base_url: str = Field(description="OpenAI 兼容 API base_url")
    api_key: SecretStr = Field(description="API Key（必填）")
    model: str = Field(description="模型名")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)  # 采样温度，默认 0.1 求稳定


# Neo4j 连接配置：默认值对应 docker-compose.yml 本地开发配置
class Neo4jSettings(BaseModel):
    """Neo4j 连接（默认值对应 docker-compose.yml 本地开发配置）。"""

    model_config = ConfigDict(extra="ignore")  # 忽略未声明的多余字段

    uri: str = "bolt://localhost:7687"  # Bolt 连接地址，默认本地
    user: str = "neo4j"  # 数据库用户名
    password: SecretStr = SecretStr("tcmkg-dev-password")  # 密码（密文），本地开发默认值
    database: str = "neo4j"  # 数据库名


# 本地嵌入模型配置：bge-m3（ADR-004），model_name 支持本地目录路径
class EmbeddingSettings(BaseModel):
    """本地嵌入模型（bge-m3，ADR-004）。model_name 支持本地目录路径。"""

    model_config = ConfigDict(extra="ignore")  # 忽略未声明的多余字段

    # 默认使用模型仓库名；部署环境可通过 TCM_EMBEDDING__MODEL_NAME 指向缓存目录。
    # 不把开发者本机路径写进生产配置，容器和 CI 均可复用该默认值。
    model_name: str = "BAAI/bge-m3"  # 嵌入模型名，可为本地目录
    dim: int = Field(default=1024, ge=1)  # 向量维度，bge-m3 为 1024
    device: str = "cpu"  # 推理设备：cpu 或 cuda
    batch_size: int = Field(default=32, ge=1)  # 单批编码文本条数


# FastAPI 服务配置：监听地址/端口/CORS 源
class ApiSettings(BaseModel):
    """FastAPI 服务（Phase F）。"""

    model_config = ConfigDict(extra="ignore")  # 忽略未声明的多余字段

    host: str = "127.0.0.1"  # 监听地址，默认仅本机访问
    port: int = Field(default=8000, ge=1, le=65535)  # 监听端口，默认 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])  # 跨域白名单
    # 运行环境标识，仅允许 development/staging/production 三种取值
    environment: str = Field(default="development", pattern="^(development|staging|production)$")
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)  # 单请求超时（秒）
    max_chat_steps: int = Field(default=8, ge=1, le=32)  # Agent 对话循环最大步数
    max_graph_limit: int = Field(default=300, ge=1, le=2000)  # 图查询单次返回上限
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)  # 每分钟限流阈值


# 可观测配置：Langfuse 自托管（经 compose profile 启动）
class ObservabilitySettings(BaseModel):
    """可观测（Langfuse 自托管，经 compose profile 启动）。"""

    model_config = ConfigDict(extra="ignore")  # 忽略未声明的多余字段

    langfuse_host: str = "http://localhost:3001"  # Langfuse 服务地址
    langfuse_public_key: SecretStr | None = None  # 公钥，None 表示未启用追踪
    langfuse_secret_key: SecretStr | None = None  # 私钥，None 表示未启用追踪


# 把海量原始中医文本，处理成知识图谱，存入 Neo4j
class PipelineSettings(BaseModel):
    """离线数据管线（Phase C）。"""

    model_config = ConfigDict(extra="ignore")  # 忽略未声明的多余字段
    # 同时开几个 LLM 线程干活。比如有 100 篇医案需要提取实体，这个值设 5 就是最多同时发 5 个请求给 LLM，处理完一个再发下一个
    llm_concurrency: int = Field(default=5, ge=1, description="C4 并发调度上限")
    # 合并重复实体时，最多召回几个候选。比如"张仲景"和"张机"可能是同一个人，系统用向量相似度召回 Top-10 候选，再从这 10 个里判断要不要合并
    align_topk: int = Field(default=10, ge=1, description="对齐召回候选数")
    # 每批往 Neo4j 写入多少条数据。如果一次写 10 万条，内存会爆、事务会超时，所以切成每 1000 条一批，分批提交
    load_batch_size: int = Field(default=1000, ge=1, description="C9 批量入库大小")


# Neo4j 独立环境配置类：离线 CLI 无 LLM 配置时单独加载 Neo4j 子系统
class _Neo4jEnvironmentSettings(BaseSettings):
    """只加载 Neo4j 子系统，供离线 CLI 在没有 LLM 时使用。"""

    model_config = SettingsConfigDict(  # 环境变量加载规则
        env_file=".env",  # 从 .env 文件读取
        env_file_encoding="utf-8",  # .env 文件编码为 UTF-8
        env_prefix="TCM_NEO4J__",  # 仅识别 Neo4j 前缀变量
        extra="ignore",  # 忽略其他前缀的环境变量
    )

    uri: str = "bolt://localhost:7687"  # Bolt 连接地址，默认本地
    user: str = "neo4j"  # 数据库用户名
    password: SecretStr = SecretStr("tcmkg-dev-password")  # 密码（密文），本地开发默认值
    database: str = "neo4j"  # 数据库名


# 嵌入独立环境配置类：align 流程仅依赖嵌入，避免被 LLM 必填项耦合
class _EmbeddingEnvironmentSettings(BaseSettings):
    """只加载嵌入子系统，避免 align 被 LLM 配置耦合。"""

    model_config = SettingsConfigDict(  # 环境变量加载规则
        env_file=".env",  # 从 .env 文件读取
        env_file_encoding="utf-8",  # .env 文件编码为 UTF-8
        env_prefix="TCM_EMBEDDING__",  # 仅识别嵌入前缀变量
        extra="ignore",  # 忽略其他前缀的环境变量
    )

    model_name: str = "BAAI/bge-m3"  # 嵌入模型名，可为本地目录
    dim: int = Field(default=1024, ge=1)  # 向量维度，bge-m3 为 1024
    device: str = "cpu"  # 推理设备：cpu 或 cuda
    batch_size: int = Field(default=32, ge=1)  # 单批编码文本条数


# 加载 Neo4j 子配置（离线 CLI 用，绕过 LLM 必填校验）
def load_neo4j_settings() -> Neo4jSettings:
    """加载 Neo4j 子配置；不触发 LLM 必填校验。"""
    return Neo4jSettings.model_validate(_Neo4jEnvironmentSettings().model_dump())  # 转存并校验


# 加载嵌入子配置（align 流程用，绕过 LLM 必填校验）
def load_embedding_settings() -> EmbeddingSettings:
    """加载嵌入子配置；不触发 LLM 必填校验。"""
    return EmbeddingSettings.model_validate(_EmbeddingEnvironmentSettings().model_dump())  # 转存并校验


# 全局配置根：聚合各分组，llm 必填、其余有本地开发默认值；从 .env 与环境变量加载
# Settings 继承的是 BaseSettings（来自 pydantic_settings），
# 它拥有自动从环境变量和 .env 文件读取配置的超能力。
class Settings(BaseSettings):
    """全局配置根：llm 必填，其余分组有本地开发默认值。"""

    model_config = SettingsConfigDict(  # 根配置的环境变量加载规则
        env_file=".env",  # 从 .env 文件读取
        env_file_encoding="utf-8",  # .env 文件编码为 UTF-8
        env_prefix="TCM_", # 只识别 TCM_ 开头的环境变量，
        env_nested_delimiter="__", # TCM_LLM__API_KEY → 自动映射为 Settings().llm.api_key
        extra="ignore", # 忽略其他环境变量，只识别 TCM_ 开头的
    )

    llm: LLMSettings = Field(  # LLM 服务配置（三项必填）
        # 无 TCM_LLM__* 环境变量时构造即报错，逐项列出缺失字段（B5.3 验收）
        default_factory=lambda: LLMSettings.model_validate({}),
    )
    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)  # Neo4j 连接配置
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)  # 嵌入模型配置
    api: ApiSettings = Field(default_factory=ApiSettings)  # FastAPI 服务配置
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)  # 可观测配置
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)  # 数据管线配置


# 进程级缓存获取全局配置（lru_cache 单例）；测试请直接构造 Settings 或清理本缓存
@lru_cache(maxsize=1)  # 进程级单例缓存
def get_settings() -> Settings:
    """进程级缓存的全局配置。测试请直接构造 Settings 或清理本缓存。"""
    return Settings()  # 首次调用构造，之后命中缓存
