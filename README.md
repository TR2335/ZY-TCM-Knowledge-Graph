# 本草通 TCM AI Knowledge Engineering Platform

面向 AI 应用、Agent、RAG 工程师面试的中医知识工程平台。项目把多来源中医资料加工为可追溯知识图谱，并通过 GraphRAG Agent 提供带证据、可审计、具备安全策略的问答能力。

## 面试亮点

- **Evidence-first GraphRAG**：每个回答关联证据 ID、来源、抽取方法与置信度，支持引用回溯。
- **混合检索**：关键词、向量和图谱邻居召回，使用 Reciprocal Rank Fusion 合并结果。
- **安全策略即代码**：采集、工具、API 和最终回答四层拦截风险药材、配伍禁忌与剂量泄露。
- **可恢复 Agent**：显式状态、步骤上限、超时、降级和结构化工具协议，便于重试与审计。
- **数据发布治理**：staging → 质量门禁 → 安全审核 → 版本化发布，支持回滚。
- **可观测与评估闭环**：请求 ID、Langfuse trace、检索命中率、引用完整性和安全通过率。

## 架构

```text
数据源 → 解析/规范化 → LLM 抽取 → 实体对齐 → 质量门禁 → Neo4j
                                                     ├─ 全文索引
用户 → FastAPI v1 → Agent 编排 → 混合检索/RRF ────────┤
                                  → 安全策略/证据拼装 ──┘
```

后端位于 `backend/`，前端位于 `frontend/`，详细生产设计见 [docs/PRODUCTION_DESIGN.md](docs/PRODUCTION_DESIGN.md)。

## 运行方式

```bash
cp .env.example backend/.env
cd backend
uv sync
uv run tcm serve
```

仅查看设计和数据管线不需要启动服务。Neo4j、Langfuse 与完整容器编排见 `docker-compose.yml`。

## 面试定位

该项目适合作为“知识工程 + GraphRAG + Agent 安全治理”的完整案例。展示时建议按 **数据可信 → 检索有效 → Agent 可控 → 服务可运维** 四条链路讲解，并用 `docs/INTERVIEW_PREP.md` 准备指标与追问。

## 数据来源与免责声明 / Data Sources & Disclaimer

- `data/curated/` 下的安全清单（十八反、十九畏、妊娠禁忌、毒性排除）与 `data/aligned-*/decisions.jsonl` 等人工审核记录，部分条目引用《中国药典》2020 年版及其【注意】项，并参考 OpenKG、BATMAN-TCM、SymMap 等公开数据集；实体类目参照国标 GB/T 15657 的目录结构组织。
- 上述第三方资料的版权归原机构所有，本项目相关内容仅限研究与教育用途。
- 本项目不构成任何医疗建议；涉及毒性药材、配伍禁忌与剂量的内容，请以官方标准和执业医师意见为准。

## 许可证

代码采用 MIT（详见 [LICENSE](LICENSE)）；第三方中医资料的版权与使用范围以各数据源许可为准。
