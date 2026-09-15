# TCM AI Knowledge Engineering Platform 生产级设计

## 目标

把当前垂直切片升级为可演示、可扩展、可审计的知识工程平台：回答必须有证据，风险必须可解释，数据必须可回滚，服务必须可观测。

## 核心链路

1. **采集与标准化**：保留 source_id、版本、章节、页码和抓取时间；解析结果以不可变 artifact 保存。
2. **抽取与对齐**：LLM 只负责候选结构化抽取，Pydantic ontology 校验字段；实体对齐保留候选分数和人工覆盖入口。
3. **质量门禁**：检查必填字段、关系端点、重复率、来源覆盖、毒性字段和异常剂量；未通过的数据只能进入 staging。
4. **版本化发布**：以 dataset_version 标识一次发布，完成安全审核后切换 active version，保留上一版本支持回滚。
5. **Evidence-first GraphRAG**：检索返回 `Evidence`，包含 evidence_id、片段、来源、实体、分数和证据类型；生成器只能引用检索到的 evidence_id。
6. **安全策略即代码**：`SafetyDecision` 记录等级、命中规则、是否遮蔽剂量及用户提示，统一用于工具、API 和最终回答。

## 在线 Agent

推荐状态机：`classify → retrieve → verify_evidence → safety_gate → compose → final_guard`。每一步都写入 trace，受 `max_steps`、单步超时和总超时约束；LLM 失败时降级为证据列表或规则回答，不能编造医学结论。

## 混合检索

精确/全文检索、向量检索和图谱扩展分别得到排序列表，使用 RRF 融合：`score(d)=Σ 1/(k+rank_i(d))`。结果按实体去重，并在返回前执行来源与安全过滤。Neo4j 负责实体、关系、全文和向量索引；大规模部署时可替换为专用向量库而不改变 `Retriever` 协议。

## API v1

- `GET /api/v1/health`：进程存活。
- `GET /api/v1/ready`：检查 Neo4j、LLM/嵌入依赖和当前数据版本。
- `POST /api/v1/chat`、`GET /api/v1/chat/stream`：同步与 SSE 问答。
- `GET /api/v1/search`：返回证据化检索结果。
- `GET /api/v1/entities/{label}/{name}`、`/neighbors`：实体与邻居。
- `POST /api/v1/feedback`：收集引用正确性、安全性和有用性反馈。
- `GET /api/v1/stats`：数据规模、版本和检索指标。

所有响应携带 `request_id`；错误统一为 `{code,message,request_id}`。旧 `/api/*` 路由保留兼容一段时间。

## 安全与运维

默认拒绝任意 text2cypher；Agent 使用领域工具，Cypher 仅供管理员只读调试。生产环境加入认证、限流、输入长度限制、密钥注入、审计日志和脱敏。指标包括 p50/p95 延迟、检索召回、引用覆盖率、拒答率、危险内容拦截率和 LLM 成本。

## 交付阶段

- **P0**：配置去硬编码、API v1 契约、健康/就绪探针、request_id、统一错误。
- **P1**：Evidence/Safety 契约、混合检索接口、数据质量报告、容器化服务。
- **P2**：真实 LangGraph 状态机、checkpoint、SSE 和反馈闭环。
- **P3**：发布审批、版本回滚、离线评估集和 CI 门禁。
- **P4**：多租户、专用向量库、灰度发布与高可用。
