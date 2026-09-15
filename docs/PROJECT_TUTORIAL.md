# 本草通项目教程：先懂架构，再读源码

这是一份面向 AI 应用、Agent、RAG 工程师面试的源码实战教程。学习顺序是：先看一条完整请求，再按目录拆文件，最后用面试语言复述设计取舍。

## 一、先记住项目的一句话

本草通不是“调用大模型回答中医问题”的聊天机器人，而是一个 **证据优先、图谱增强、策略可控的知识工程平台**：把多来源资料加工成带 provenance 的结构化知识，使用 Neo4j 做实体和关系查询，再由 Agent 组织证据回答；涉及毒性和剂量时，策略代码优先于模型输出。

## 二、整体架构

```text
原始资料
  ↓ download / parse
统一中间 JSONL
  ↓ extract（LLM + Pydantic）
实体、关系、来源、置信度
  ↓ align / quality gate / safety policy
版本化可发布数据
  ↓ load
Neo4j：节点、关系、全文索引、向量索引
  ↓
FastAPI v1 → Agent → 领域工具 → 混合检索 → Evidence → SafetyDecision → 答案
```

一次在线问答的顺序：

1. `POST /api/v1/chat` 生成 `request_id`。
2. Agent 根据问题选择 `get_herb`、`get_formula`、辨证或禁忌工具。
3. 工具只返回结构化查询结果；`sanitizer.py` 先掩码毒性剂量。
4. API 同时生成 `Evidence` 列表，保留 evidence_id，便于引用与评估。
5. Agent 产出答案后执行最终安全兜底；API 返回 `SafetyDecision`、延迟和引用。

## 三、目录怎么读

### 1. `backend/src/tcm_kg/ontology/`

这里是领域合同，不是普通常量目录。

- `entities.py`：药材、方剂、证候、症状等 Pydantic 实体模型。
- `relations.py`：关系端点、属性和约束。
- `enums.py`：毒性、安全等级等枚举，避免字符串散落。
- `provenance.py`：来源、方法、置信度、定位信息。
- `export.py`：把本体导出成 JSON Schema，供抽取和评审。

面试说法：先定义 ontology，再让抽取、对齐、入库和 API 共享同一份类型合同，减少“模型说了一套、数据库存了一套”的漂移。

### 2. `pipeline/`

数据管道是离线知识工程主线。

- `download.py` / `sources.py`：登记数据源，下载并保留源文件。
- `parse.py`、`parsers/`：把网页、PDF、表格和旧 txt 统一成中间格式。
- `llm.py`：OpenAI 兼容接口和结构化输出协议；LLM 只做候选抽取。
- `extract.py`：并发调用结构化 LLM，失败重试并写 provenance。
- `align.py`：嵌入召回 + 规则/LLM 精判，产出 same、related、different 和人工审核队列。
- `load.py`：执行 L1 排除、L2 caution 分级，再写入 Neo4j。
- `embedding.py`：bge-m3 编码协议；默认模型名是 `BAAI/bge-m3`，部署可通过环境变量换成本地缓存。

关键原则：LLM 是概率组件，不能直接成为事实源；事实要经过 schema 校验、来源记录、对齐和安全门禁。

### 3. `graphdb/repo.py`

`Neo4jRepo` 是数据访问层：

- 节点和关系用参数化 Cypher + `MERGE`，保证幂等加载。
- 实体先写、关系后写，保证关系端点已经存在。
- provenance 展开为 `prov_*` 属性，图上可审计。
- `ping()` 只做连通性探针，给 `/api/v1/ready` 使用。

不要让业务层到处拼 Cypher；新增查询优先在 repository 或领域工具里封装。

### 4. `agent/`

- `tools.py`：Agent 的唯一领域入口。搜索、药材、方剂、辨证、配伍禁忌都在这里。
- `sanitizer.py`：安全输出边界。毒性药材隐藏 `dose_range`，caution 方剂隐藏用法和毒性组成剂量。
- `graph.py`：当前是显式 JSON tool-loop，带最大步数和未知工具错误回传；没有 LLM 时使用 `RuleBasedAgent` 降级。

当前生产工厂用 `AgentTools(..., allow_raw_cypher=False)`，原始 Cypher 不开放给 Agent。`run_cypher` 仍保留给受控调试和兼容场景。

### 5. `retrieval/`

`hybrid.py` 定义可替换的 `Retriever` 协议和 `HybridRetriever`：

- lexical：精确名、别名、全文索引。
- vector：bge-m3 语义向量。
- graph：实体邻居和关系扩展。
- `reciprocal_rank_fusion`：按 `1/(k+rank)` 融合，避免三个召回器分数尺度不同。

这是 RAG 项目里很重要的工程边界：以后把 Neo4j 向量换成 Milvus 或 pgvector，只替换 adapter，不改 API 和 Agent。

### 6. `api/`

- `contracts.py`：稳定传输协议。`Evidence` 表示可回溯事实，`SafetyDecision` 表示命中的安全规则，`ChatResponse` 组合答案、引用、追踪和延迟。
- `app.py`：应用工厂、依赖注入、API v1、旧路径兼容、CORS、request_id、错误结构、SSE。

重点接口：

```text
GET  /api/v1/health
GET  /api/v1/ready
POST /api/v1/chat
GET  /api/v1/chat/stream?q=...
GET  /api/v1/search?q=...
GET  /api/v1/entities/{label}/{name}
GET  /api/v1/entities/{label}/{name}/neighbors
POST /api/v1/feedback
GET  /api/v1/stats
```

旧 `/api/chat`、`/api/herb/*`、`/api/formula/*`、`/api/graph` 继续保留，便于前端平滑迁移。

### 7. `frontend/index.html`

当前是轻量演示前端：聊天、图谱 SVG、毒性节点高亮、快捷问题。它调用 v1 health/chat，并展示证据 ID。面试时应坦诚说明：单文件前端适合垂直切片，长期生产可迁移到 Next.js、组件库和 G6/AntV。

### 8. `cli.py`、`docker-compose.yml`

CLI 把 `download → parse → extract → align → load → serve` 串起来，是数据工程入口。Compose 现在包含 Neo4j、后端和 Nginx 前端，并预留 Langfuse observability profile。

## 四、安全策略为什么是亮点

安全不能只写在 system prompt。项目采用四道边界：

1. **数据层**：L1 直接排除高风险生品或证据不足方剂，L2 标记 `caution`。
2. **工具层**：查询结果立即经过 `sanitize_*`，剂量字段先变成 MASK。
3. **Agent 层**：最终答案含 MASK 但缺少就医提示时，`_e7_guard` 自动补充。
4. **API 层**：序列化前再次消毒，并把规则写进 `SafetyDecision`。

这套设计能回答面试官的追问：如果模型绕过提示词怎么办？模型永远拿不到未经策略处理的敏感字段，且最终出口还有兜底。

## 五、如何面试讲 3 分钟

“我做的是一个中医知识工程平台。离线侧把多来源资料解析成统一 JSONL，用 Pydantic ontology 约束实体和关系，LLM 只做结构化候选抽取，再通过向量召回、规则精判和人工审核完成实体对齐，最后做毒性安全门禁并幂等写入 Neo4j。在线侧不是让模型自由回答，而是让 Agent 通过领域工具检索图谱，全文、向量和图扩展用 RRF 融合，返回带来源的 Evidence。对毒性和剂量，我把策略做成代码，在数据、工具、Agent、API 四个边界执行。服务提供 v1 API、SSE、request_id、readiness、统一错误和反馈接口，后续可以接 LangGraph checkpoint、评估集和版本发布回滚。”

## 六、面试追问怎么答

- **为什么图谱而不是纯向量？** 向量适合语义相似，图谱适合关系约束、配伍禁忌、可解释路径；两者组合降低幻觉。
- **为什么 RRF？** 不要求关键词分数、向量相似度和图路径分数同尺度，工程上稳定且容易解释。
- **为什么 LLM 不直接写库？** 概率输出必须经过 schema、provenance、对齐和质量门禁，否则错误会被永久化。
- **如何保证幂等？** 节点和关系使用 MERGE，加载分批执行，实体先行、关系后行。
- **Agent 死循环怎么办？** 显式状态、最大步数、超时、未知工具错误和规则降级；后续接 LangGraph checkpoint 做恢复。
- **怎么评估？** 检索 Recall@k、MRR，引用覆盖率、答案 groundedness、安全拦截率、p95 延迟、LLM 成本。
- **当前还有什么没完成？** 真实 HNSW reindex、完整 LangGraph checkpoint、发布审批/回滚和生产认证限流属于下一阶段；文档已明确区分，避免夸大实现。

## 七、推荐学习顺序

先读 `docs/PRODUCTION_DESIGN.md` 和本教程，再按 `ontology → pipeline → graphdb → agent → retrieval → api → frontend` 阅读。每读完一层，都回答三个问题：输入是什么、输出是什么、失败和安全边界在哪里。最后只看 `INTERVIEW_PREP.md`，尝试不看代码复述整条链路。
