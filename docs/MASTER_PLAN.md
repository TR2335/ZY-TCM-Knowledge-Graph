# 本草通 2.0（tcm-kg）· 中医知识图谱重做项目总体执行计划

| 项 | 值 |
|---|---|
| 文档版本 | v1.3（2026-08-21 修订：ADR-013 精细化——毒/险方剂剂量隐藏 + 明确标注 + 医师开具引导） |
| 文档性质 | 项目唯一执行事实源（Single Source of Truth） |
| 项目代号 | tcm-kg 2.0（重做，不继承旧图谱数据结构） |
| 旧项目 | 旧版中医知识图谱项目（本地归档，保留只读，M6 后归档） |

---

## 0. 文档使用规则（如何"按文档执行"）

1. **认领**：开始任何任务前，把对应任务状态从 ☐ 改为 ◐。
2. **执行**：严格按任务的"子步骤"顺序推进；子步骤可以再细化，但不可跳过验收标准。
3. **完结**：验收标准全部满足后标记 ☑，并在 §11 执行日志追加一行。
4. **门禁**：M0 / M2 / M4 是用户决策门，到达后停下等确认，不得自行跨阶段。
5. **变更**：先改本文档再动代码；任何计划变更在执行日志记录原因。
6. **状态图例**：☐ 未开始 ｜ ◐ 进行中 ｜ ☑ 已完成 ｜ ⏸ 阻塞（附原因）

---

## 1. 项目定位与目标

### 1.1 一句话定位
以**本体先行、多源融合、全程可溯源**的方式重做中医知识图谱，用 **Agentic GraphRAG** 提供高质量问答。

### 1.2 与旧项目的本质区别

| 维度 | 旧项目 | 本项目 |
|---|---|---|
| Schema | 爬取结果反推，5 类实体 | 本体先行，标准对齐（GB/T 15657 + ICD-11 TM1 + 中医药学名词） |
| 数据 | 单源爬取、无对齐、无溯源 | 多源批量下载 + 三级对齐 + provenance 全链路 |
| 知识深度 | 药材百科级 | 补全证候/治法/七情配伍/十八反十九畏/妊娠禁忌 |
| 问答 | 10 节点固定流水线 | 1 个研究型 Agent + 6 工具自主检索（含辨证 match_patterns） |
| 质量 | 肉眼判断 | 黄金评测集 + promptfoo 持续回归 |

### 1.3 量化成功指标（M6 最终验收）

1. **图谱规模**：中药 ≥ 600、方剂 ≥ 5000、证候 ≥ 200、关系 ≥ 50000（全部带 provenance）
2. **数据质量**：对齐后重复实体率 < 0.5%；矛盾三元组清零或显式豁免；关键字段（名称/功效/分类）覆盖率 ≥ 95%；**图谱内排除清单药材出现于任何方剂 = 0（ADR-013）**
3. **问答质量**：黄金评测集 ≥ 100 题，通过率 ≥ 85%，其中安全禁忌类（含毒性警示与剂量隐藏）100% 正确
4. **工程质量**：核心模块测试覆盖 ≥ 80%；全新环境 `docker compose up` 一键复现

---

## 2. 技术选型决策记录（ADR）

| # | 决策 | 结论 | 一句话理由 | 回退方案 |
|---|---|---|---|---|
| ADR-001 | 运行时 | Python 3.13 + uv | 性能与类型生态最优，uv 是当前标准 | 降 3.12 |
| ADR-002 | Agent 框架 | LangGraph 1.x | tool-loop 编排最强，checkpointer/流式成熟 | 锁版本 + 薄封装 |
| ADR-003 | 存储 | Neo4j 2025（图 + HNSW 向量一库） | 万级数据无需独立向量库，Cypher 一条语句混合检索 | 外挂 Qdrant |
| ADR-004 | Embedding | 本地 bge-m3（1024 维） | 中文检索 SOTA、免费、数据不出本机 | 换 API 嵌入 |
| ADR-005 | 前端 | Next.js 15 + React 19 + Tailwind 4 + AntV G6 5.x | 真前后端分离，AI 聊天组件生态成熟 | Nuxt 3 |
| ADR-006 | LLM 结构化输出 | 原生 Structured Outputs（JSON Schema） | 模型层保证 schema，删除解析修复链 | json mode + 校验重试 |
| ADR-007 | 可观测 | structlog + Langfuse 自托管 | LLM 全链路 trace 是调 Agent 必备 | 仅 structlog |
| ADR-008 | 评测 | promptfoo + LLM-as-judge | 评测版本化、可回归 | pytest 自写 harness |
| ADR-009 | 部署 | Docker Compose（neo4j + backend + frontend） | 单机场景最优 | K8s 预留 |
| ADR-010 | 数据获取策略 | 优先批量下载开放数据集，爬虫仅兜底 | 调研证实 SymMap/BATMAN 等可全量下载，质量高于爬取 | 增加爬虫适配器 |
| ADR-011 | 中成药建模 | Formula.formula_type 属性，不设独立实体 | COMPOSED_OF 等关系完全复用；避免同源实体拆分（藿香正气散/水） | 独立 PatentMedicine 实体（需强类型过滤时再拆） |
| ADR-012 | 辨证推理 | match_patterns 工具：主症必配 + 次症加权（GB/T 16751.2 主症/次症标准） | 教科书辨证法，可解释可验证；朴素症状计数区分度不足 | LLM 自由推理（不可验证，弃） |
| ADR-013 | 安全治理（用户决策） | 三层防线：L1 含毒性管理品种生品的方剂不入库 → L2 safety_level 分级 → L3 答案强制警示与**剂量隐藏**（caution 方剂/有毒药材不输出剂量与用法，明确标注，引导医师开具，详见 ontology §7.4） | 安全优先于数据完整性；剂量留库供审计、出口双点掩码（工具层+API 层） | 仅答案层警示不过滤（风险不可控，弃） |

---

## 3. 系统架构

### 3.1 分层与依赖铁律

```
L0 离线管线 pipeline/   crawl→download→parse→extract→align→load→index
L1 前端 frontend/       Next.js，只经 HTTP/SSE 访问 L2
L2 API api/             FastAPI，只调 L3
L3 Agent agent/         LangGraph，只经工具访问 L4
L4 存储 graphdb/        Neo4j 驱动 + 仓储层
横切 core/              settings / logging / ontology（被所有层引用，自身零依赖）
```

**铁律**：只允许上层引下层；pipeline 与 api 互不引用；ontology 不依赖任何层。

### 3.2 数据流

- **在线**：问题 → SSE → Agent 循环 {vector_search / fulltext / text2cypher / expand_neighbors / community_summary / match_patterns} → 答案（含溯源）
- **离线**：数据源下载/爬取 → 解析归一 JSONL → LLM 抽取（带 provenance）→ 三级对齐 → MERGE 入库 → bge-m3 重嵌 → HNSW 索引

### 3.3 三个关键架构决策

1. **图与向量一库**（ADR-003）：向量是节点属性，HNSW 索引随图更新，彻底消灭旧项目 FAISS 与 Neo4j 的漂移问题。
2. **单 Agent + 工具集**（ADR-002）：旧项目 10 节点流水线是为弱模型设计的；现在一个 ReAct 循环 + 5 工具可处理固定流水线无法处理的跨实体复杂问题。
3. **provenance 全链路**：每个节点/关系带 source/confidence/extracted_at/method，为评测归因和冲突回滚提供数据基础。

---

## 4. 本体设计摘要（详见 ontology-design.md）

### 4.1 设计原则

1. **标准对齐**：病证分类以 GB/T 15657-2021 为主骨架，ICD-11 TM1 作国际对齐层（`icd11_code` 字段）；术语规范名对齐《中医药学名词》。
2. **证据可溯**：所有节点与关系带 provenance 四元组。
3. **语义严格**：每个关系有定义域/值域/基数约束，入库前校验。
4. **枚举属性优先**：性味归经毒性做枚举属性（工程验证过的模式），不做关系爆炸。
5. **版本化**：本体 semver，schema 变更走迁移脚本。

### 4.2 实体类型总表（12 类）

| 实体 | Label | 规范名来源 | 关键属性 |
|---|---|---|---|
| 中药 | Herb | 2020 药典名 | nature/flavor/meridian/toxicity/dose_range |
| 方剂 | Formula | 方剂学教材/数据集 | formula_type(经方/时方/中成药)/efficacy（v1.1：中成药为属性，ADR-011） |
| 中医疾病 | DiseaseTCM | GB/T 15657 A 码 | code/icd11_code |
| 证候 | Pattern | GB/T 15657 B 码 | code/icd11_code |
| 中医症状 | SymptomTCM | SymMap | — |
| 西医疾病 | DiseaseWM | ICD-11 1-24 章 | icd11_code |
| 西医症状 | SymptomWM | SymMap | — |
| 治法 | TreatmentMethod | GB/T 15657 C 码 | code |
| 功效 | Effect | 中医药学名词 | — |
| 化学成分 | Component | PubChem | 二阶段 |
| 出处 | Source | 典籍/药典/数据集 | type |
| 分类 | Category | GB/T 层级 | tree_path |

### 4.3 关系类型总表（20 种，详见 ontology-design.md §3）

核心：`COMPOSED_OF{dose,role}` / `TREATS_PATTERN` / `HAS_SYMPTOM{is_primary}` / `HAS_EFFECT` / `CONTRAINDICATED_WITH`（十八反十九畏）/ `PREGNANCY_CONTRA` / `SYNERGISTIC_WITH` / `RESTRAINS`（七情）/ `CORRESPONDS_TO`（中西医映射）/ `PROCESSED_FROM`（炮制）等。

### 4.4 待定决策清单（M0 前裁决）

- D1 常见炮制品独立节点 vs 属性（建议：独立节点 + PROCESSED_FROM）
- D2 剂量单位标准化（建议：统一 g，古典单位换算表）
- D3 DiseaseTCM 与 DiseaseWM 的映射粒度（建议：SymMap + ICD-11 双编码）

---

## 5. 数据源策略（详见 research/A1 调研报告）

### 5.1 冷启动组合（第一版全部离线批量获取，无需爬虫）

| 源 | 用途 | 规模 | 协议 |
|---|---|---|---|
| SymMap v2.0 | 主干：草药+性味归经+中医症状+证候+西医映射 | 698 草药/2285 中医症状/233 证候 | 需引用 NAR 2019 |
| BATMAN-TCM 2.0 | 方剂层 | 54832 复方 | CC BY-NC 4.0 |
| nmm-data | 药典目录 = 实体对齐锚点 | 616 条 | CC-BY-4.0 |
| OpenKG 174 方剂 | 君臣佐使 + 药典标准化剂量金标准 | 174 方 | 共享协议 |
| GB/T 15657-2021 | 病证分类骨架 | 1369 病/2060 证候 | 国标公开 PDF |
| GB/T 16751.2 | 证候主症/次症诊断标准（辨证加权依据，ADR-012） | SymMap 233 证候范围内结构化 | 国标公开 PDF |
| 十八反十九畏+妊娠禁忌 | 安全禁忌边（人工结构化） | 37 味/9 对/99 味 | 自建 |
| 旧项目已爬 txt | 功效/主治文本增强（LLM 抽取） | 2000+ | 自有 |

### 5.2 版权红线
- BATMAN/HERB/TCM-MKG 为 CC BY-NC：**禁止商用**，学术研究注明引用。
- 药典原文仅用于抽取与校验，不分发原文。
- 所有来源在 Source 节点登记，答案溯源时展示。

---

## 6. WBS 详细分解

> 每个任务卡片：目标 / 依赖 / 复杂度(S≤半天 M≤2天 L≥3天) / 子步骤 / 产物 / 验收

### Phase A：本体与设计（→ M0）

#### A1 标准与数据源调研 ☑
- A1.1 ICD-11 TM1 结构与获取（Browser/API/OAuth2/CDN 离线包）✓
- A1.2 GB/T 15657-2021 病证分类体系（A/B/C 码，1369 病 + 2060 证候）✓
- A1.3 术语标准（中医药学名词/TCMLS-SN）与既有中医本体对比 ✓
- A1.4 可批量下载数据源盘点（SymMap/BATMAN/HERB/TCMID/nmm-data/OpenKG）✓
- 产物：`docs/research/A1-standards-and-sources-survey.md`
- 验收：三类来源各有 ≥2 个具体来源且含获取方式与协议状态；有对比表

#### A2 实体类型设计 ☑
- A2.1 从调研提取候选实体全集（参考 TCMLS-SN 顶层 + KerroKapple 精简模式 + 学术本体）✓
- A2.2 逐类定义：中文名/Label/定义/canonical 命名规范/必选属性 ✓
- A2.3 边界裁决表：炮制品（独立节点）、性味归经（枚举属性）、证候独立建类 ✓
- 产物：`docs/ontology-design.md` §2

#### A3 关系类型设计 ☑
- A3.1 语义关系全集（七情/禁忌/组成/主治/映射/溯源）✓
- A3.2 每关系定义域/值域/基数/边属性 ✓
- A3.3 一致性检查：所有实体至少出现在一个关系；无语义重复关系 ✓
- 产物：`docs/ontology-design.md` §3

#### A4 属性与 provenance 规范 ☑
- A4.1 各实体属性表（类型/单位/枚举/必填）✓
- A4.2 枚举规范：性（寒热温凉平）/味（酸苦甘辛咸涩淡）/归经（十二经）/毒性分级 ✓
- A4.3 provenance 四元组 + 置信度规则 ✓
- A4.4 本体版本化方案 ✓
- 产物：`docs/ontology-design.md` §4-§6

#### A5 数据源决策 ☑
- A5.1 冷启动组合确定（§5.1 表）✓
- A5.2 版权红线与引用义务清单 ✓
- 验收：每个冷启动源有明确下载入口与使用边界

#### A6 评测集设计 ☑
- A6.1 题型分类框架与配比（§9）✓
- A6.2 20 条种子题（含预期答案要点）✓
- 产物：`docs/eval/golden-set-v0.md`

#### A7 设计评审 → **M0 门** ☑
- A7.1 汇总设计文档包（本体 spec + 数据源决策 + 评测规范）✓
- A7.2 用户评审，裁决 §4.4 待定决策 ✓（用户 2026-08-21 确认"可以执行"= 设计冻结；D1/D2/D3 按设计建议裁决：炮制品独立节点、通行剂量换算+raw_dose、SymMap+ICD-11 双编码）
- 验收：用户书面确认"设计冻结" ✓

#### A8 本体代码化（M0 通过后立即执行）☑
- A8.1 `backend/src/tcm_kg/ontology/` 包结构（models/enums/relations）
- A8.2 Pydantic 模型：每实体/关系一个类，Field 约束 + docstring
- A8.3 枚举定义（性味归经毒性等）
- A8.4 自检：导出 JSON Schema + 合法/非法样例单测
- 验收：mypy strict 通过；全部枚举有测试；schema 与 ontology-design.md 零偏差

### Phase B：工程基建（→ M1）

#### B1 仓库初始化 ☑
- B1.1 `tcm-kg/` 内 git init + .gitignore（Python/Node/data/models）
- B1.2 目录骨架：backend/frontend/data/docs/scripts
- 验收：目录结构与 §10 一致

#### B2 依赖管理 ☑
- B2.1 `uv init --python 3.13` + `.python-version`
- B2.2 依赖分组：runtime / pipeline / dev（见 §10.3）
- B2.3 `uv lock` + `uv sync` 验证
- 验收：干净环境一键装齐

#### B3 Neo4j 容器 ⏸（交付物完成，`docker compose config` 校验通过；`make up` 运行时验收待用户手动启动 Docker Desktop 后执行 `make up && make init-db`）
- B3.1 docker-compose.yml：neo4j 2025 + APOC + GDS 插件
- B3.2 内存/密码环境变量；数据卷
- B3.3 `scripts/init_constraints.cypher`：12 类实体 name UNIQUE 约束 + 关系存在约束
- B3.4 Makefile：up/down/logs/neo4j-shell
- 验收：浏览器 7474 可访问；约束已建（`SHOW CONSTRAINTS`）

#### B4 质量工具链 ☑
- B4.1 ruff（line 100，规则 E/F/I/B/UP/SIM）
- B4.2 mypy strict
- B4.3 pre-commit
- 验收：`make check` 全绿

#### B5 配置层 ☑
- B5.1 pydantic-settings 嵌套分组（llm/neo4j/embedding/api/pipeline）
- B5.2 `.env.example` 完整化
- B5.3 单测：缺必填报错清晰、默认值正确
- 验收：故意删配置启动，报错指明缺失项

#### B6 日志与追踪 ◐（B6.1/B6.2 完成；B6.3 LLM 埋点按架构决策随 Phase E/E3 统一封装时接入——避免在无 LLM 调用方时写死封装；验收"Langfuse 完整 trace"顺延至 E3 后补验）
- B6.1 structlog（JSON 与 pretty 可切换）
- B6.2 Langfuse 自托管进 compose + SDK 初始化
- B6.3 LLM 调用统一封装埋点
- 验收：一次 LLM 调用在 Langfuse 有完整 trace

#### B7 CLI 骨架 ☑
- B7.1 typer app：download/parse/extract/align/load/reindex/chat
- B7.2 每子命令 docstring + `--help`
- 验收：`tcm --help` 与各子命令帮助可用

### Phase C：数据管线（→ M2，最重阶段）

#### C1 冷启动数据获取 ☑（C1.8 除外：GB/T 16751.2 无公开电子版，获取途径已登记，C2.4 人工结构化时按纸本/教材执行）
- C1.1 下载 SymMap v2.0 七类 xlsx → `data/raw/symmap/` ✓
- C1.2 下载 BATMAN-TCM 2.0 复方与关联 tsv → `data/raw/batman/` ✓（经 HuggingFace 官方镜像 f-galkin/batman2，batman_ingredients.csv >1GB 属二阶段内容暂缓）
- C1.3 克隆 nmm-data 药典目录 csv → `data/raw/nmm/` ✓
- C1.4 下载 OpenKG 174 方剂 xlsx → `data/raw/openkg/` ✓
- C1.5 获取 GB/T 15657-2021 PDF（cacm.org.cn 公开版）→ `data/raw/gbt15657/` ✓（原址 404，经 Internet Archive 快照获取）
- C1.6 旧项目已爬 txt 复制（中药/方剂）→ `data/raw/legacy/` ✓（1474 中药 + 704 方剂）
- C1.7 manifest.jsonl 记录每源版本/日期/hash ✓（2190 条）
- C1.8 获取 GB/T 16751.2《中医临床诊疗术语·证候部分》→ `data/raw/gbt16751/`（主症/次症数据源）◐ 途径登记于 sources.py 文档
- 验收：全部冷启动源落盘且有 manifest ✓

#### C2 禁忌与安全清单人工结构化 ◐（C2.1/2/3/5 初稿完成待 C2.6 用户抽查；C2.4 挂起）
- C2.1 十八反 3 组 37 味 → `data/curated/shiba_fan.csv`（药对+原文出处）✓ 初稿 38 味（按药典 2020 独立条目展开：含炮制品条目法半夏/姜半夏/清半夏/制川乌/制草乌/炙甘草/红参与红大戟，与通行"37 味"口径差异已在执行日志说明）
- C2.2 十九畏 9 对 → `data/curated/shijiu_wei.csv` ✓ 初稿 9 对（犀角/密陀僧药典未收载，仅文献级记载边）
- C2.3 妊娠禁忌（禁/忌/慎三级）→ `data/curated/pregnancy_contra.csv` ✓ 初稿 73 味（禁用 44 + 慎用 29，药典 2020【注意】项口径）
- C2.4 SymMap 233 证候的主症/次症结构化（依据 GB/T 16751.2 诊断标准）→ `data/curated/pattern_primary_symptoms.csv`（HAS_SYMPTOM.is_primary 数据源，辨证加权用）⏸ 依赖 GB/T 16751.2（无公开电子版），随 C3/C6 数据管线推进时按纸本/教材批量结构化
- C2.5 毒性排除清单：国务院《医疗用毒性药品管理办法》品种 + 药典大毒标记 → `data/curated/toxic_exclusion.csv`（L1 过滤依据，ADR-013；含生品/炮制品区分标记）✓ 初稿：官方 28 种逐字（国务院令第 23 号 nhc.gov.cn 原文）+ 炮制品/有毒药 22 行（L2 分级参考），l1_excluded 语义单测锁定
- C2.6 双人交叉核对（用户抽查）⏸ 待用户抽查四个 CSV
- 验收：条目数与权威文献一致 ✓（单测锁定：28 种官方目录逐字、9 对、三组十八反、PregnancyLevel/Toxicity 枚举零偏差）；用户抽检 ⏸

#### C3 解析归一管线 ☑（六源全部跑通；SMDE 西医疾病暂缓至 C6/二阶段 ICD-API 桥接）
- C3.1 SourceParser 抽象：每源一个 parser 类 → 统一中间格式 JSONL（对齐 A8 本体模型）✓ envelope 三态（entity/relation/quarantine）+ 行级隔离 + 首条 Source 实体 + provenance 约定
- C3.2 SymMap parser（草药/症状/证候/中西医映射）✓ SMHB→Herb 698（性味串内嵌毒性标记解析）| SMTS→SymptomTCM 1397（仅本义术语，同义术语留 C6）| SMMS→SymptomWM 1148 | SMSY→Pattern 233；SMDE（西医疾病）⏸ ICD-10≠ICD-11，需 ICD-API 桥接（C6/二阶段）
- C3.3 BATMAN parser（复方-草药组成）✓ 流式 csv（525MB 不整载）；拼音→中文映射（herbs.csv synonyms，2053 味）；Formula 31846（54832 行同名去重）+ COMPOSED_OF 280729；未映射拼音药边隔离 111438（约 26%，设计决策：不建拼音 Herb 节点污染规范名空间）；**安全决策：组成可能不完整 → C9 加载时 BATMAN 源方剂一律 safety_level=caution**
- C3.4 GB/T 15657 PDF 表格提取脚本（层级码→Category 树）✓ pymupdf sort=True 阅读序提取（A 码区码先名后/B 码区名先码后的文本流差异经坐标阅读序归一）；DiseaseTCM 1258+类目/Pattern 1654+类目/TreatmentMethod 1107+类目/Category 578/BELONGS_TO 2912；实体+类目总数与官方对账 1369/2060/1168 ✓；治法不建 BELONGS_TO（§3.2 定义域零偏差）；同码异名暂弃（本体无 aliases 字段，C6 用名称+嵌入对齐）
- C3.5 nmm-data/OpenKG/curated parser ✓ nmm→Herb 616（对齐锚点）| OpenKG→Formula 174+COMPOSED_OF 1249（君臣佐使+剂量）| curated→CONTRAINDICATED_WITH 94（十八反 87+十九畏 7，犀角对按约定跳过、密陀僧保留文献级）+ PREGNANCY_CONTRA 73 + Category(妊娠禁忌)
- C3.6 每 parser 单测（固定样本文件）✓ 13 项单测（合成 fixture 12 + 真实 GB/T PDF 全量对账）；全部 envelope 回验本体模型
- 验收：每源产出 JSONL 通过 Pydantic 校验率 100%（失败进隔离清单）✓ 六源全量实测：五源 100.00% 隔离 0；BATMAN 73.72%（隔离全部为 unmapped_pinyin_herb 设计性隔离，非校验失败）；对齐前规模预演：Herb 1314/Formula 32020/Pattern 1887/关系 285057——全部超 §1.3 目标线

#### C4 LLM 抽取（旧 txt + 药典文本）◐（代码与测试全部完成；全量实跑待用户提供 LLM 配置）
- C4.1 prompt 模板：system 注入从 Pydantic 反射生成的本体 schema ✓ SYSTEM_PROMPT 领域规则（禁臆造/枚举约束/剂量保留原文）+ 抽取模型 JSON Schema 动态注入 + 正文截断 6000 字
- C4.2 Extractor：文本 → Pydantic 模型（native structured output）✓ pipeline/llm.py：StructuredLLM 协议（可注入 FakeLLM）+ LangChainStructuredLLM（ChatOpenAI with_structured_output，ADR-006）；抽取目标模型 HerbExtract/FormulaExtract（枚举直连本体）
- C4.3 并发调度：asyncio + Semaphore（默认 5，可配）✓ settings.pipeline.llm_concurrency
- C4.4 断点续跑：输入 hash 记录，重跑跳过 ✓ Checkpoint（kind+title+text sha256 → checkpoint.json）
- C4.5 失败处理：重试 2 次 → 失败清单文件 ✓ 指数退避 + failures.jsonl；转换期本体校验失败 → 隔离（不计 LLM 失败）
- C4.6 输出 `data/extracted/*.jsonl`（带 provenance）✓ legacy_{herb,formula}.jsonl；HerbExtract→Herb+Effect×n+HAS_EFFECT×n；FormulaExtract→Formula+Herb×n+COMPOSED_OF×n(raw_dose 原文,折算属 C9)+TREATS_PATTERN×n+TREATS_DISEASE×n；provenance=llm_extract/0.7
- C4.7 统计报告：成功率/字段覆盖率/耗时/token 成本 ✓ ExtractStats + format_extract_report
- 验收：schema 校验通过率 100% 落盘；关键字段覆盖率 ≥ 95% ◐ 待实跑（CLI `tcm extract`，需 backend/.env 配置 TCM_LLM__* 三项；单测 9 项全绿覆盖转换/并发/断点/重试/隔离/统计）

#### C5 抽取质检 ☐
- C5.1 自动统计：字段非空率/枚举分布/数值异常（剂量 > 500g 等）
- C5.2 低置信样本（< 0.7）抽 50 条人工复核
- C5.3 质量报告 + 修复项
- 验收：质量报告归档；修复项闭环

#### C6 对齐·召回 ☑（bge-m3：默认使用 `BAAI/bge-m3`，也支持通过环境变量配置本地缓存目录，FlagEmbedding 加载）
- C6.1 候选池：全部 canonical name + alias 用 bge-m3 嵌入（内存索引）✓ pipeline/embedding.py（Embedder 协议 + FlagModel 实现，本地模型目录缺 1_Pooling 故走 FlagEmbedding 而非 sentence-transformers）+ build_pool（parsed JSONL → 池，canonical+alias 全入，同源去重跨源保留）
- C6.2 每新实体召回 top-10 候选 ✓ recall_topk（分块点积防 3.6GB 全量矩阵，sim≥0.45 候选线实测标定）+ candidate_pairs（i<j 去重、同实体内部对排除）
- C6.3 召回评估：人工标 50 对算召回率 ✓ evaluate_recall（labeled_pairs.csv 可选输入）；**人工标注 50 对待用户参与**
- 验收：同义对召回率 ≥ 95% ◐ 待人工标注后评估（机制已就绪）

#### C7 对齐·精判 ☑（规则模式为主，LLM 可选增强）
- C7.1 LLM 精判 prompt：实体对+属性上下文 → same/related/different + 理由 ✓ LLMJudge（注入 StructuredLLM；CLI --llm 启用，未配置自动降级规则）
- C7.2 批量执行 → `data/aligned/decisions.jsonl` ✓ rule_judge 规则精判（**实测校准三轮收紧**：初版纯语义阈值 sim≥0.75→same 导致危险误合并——赤芍/白芍、茯苓/猪苓、党参/明党参、巴豆/巴豆霜（L1 生熟分界！）；最终规则：字符等价 same 0.98 → 炮制/部位词差异（制X/X霜/X粉/X叶 等 14 前缀+13 后缀）→ related 强制不合并 → 长名包含（min≥3字差≤1）same → 编辑距离≥0.85 same → 其余全部进人工审核——**自动合并宁缺毋滥**）
- C7.3 人工抽检 100 条算准确率 ⏸ 待用户抽检（Herb 池实测复跑：648 对合并候选中 616 字符等价+32 语义对，其中炮制品对已被新规则拦截进 review）
- 验收：same 判定准确率 ≥ 90% ◐ 待人工抽检

#### C8 审核队列 ☑（结构交付；首批人工裁决待用户）
- C8.1 待审清单 CSV（实体对+属性对比+LLM 建议）✓ review_queue.csv（label/a/b/source/sim/verdict/confidence/reason 全列）
- C8.2 人工裁决回流格式 ◐（decisions.jsonl 行结构已定，人工回流字段 same/different/related 覆盖 + method=human；批量工具待 C8.3 随首批裁决配套）
- C8.3 首批 ≥ 100 条裁决 + 自动合并规则沉淀 ⏸ 待用户裁决（Herb 池已产出 8256 行待审队列）
- 验收：首批审核完成；规则文档化 ◐ 规则已文档化（align.py docstring + 本日志）；首批裁决待用户

#### C9 入库加载（含安全过滤）☐
- C9.1 loader：JSONL → 参数化 Cypher MERGE（ON CREATE/ON MATCH SET provenance）
- C9.2 **L1 安全过滤：加载前比对 toxic_exclusion.csv，含清单生品的方剂整体跳过并写入 `exclusion_report.csv`（ADR-013）**
- C9.3 **L2 分级推导：入库方剂按成分毒性计算 safety_level（safe/caution）**
- C9.4 关系加载（含 dose/role 边属性）
- C9.5 幂等验证：跑两遍节点数不变
- C9.6 入库统计报告（各类节点/关系计数 vs 目标 + 排除统计）
- 验收：幂等 + 约束零违规 + 达 §1.3 规模线 + 排除报告完整可审计

#### C10 向量索引 ☐
- C10.1 嵌入文本构造（name+alias+功效摘要）
- C10.2 批量嵌入写节点属性 + 建 HNSW（dim 1024, cosine）
- C10.3 KNN 抽检（top-5 合理性）
- 验收：向量检索样例通过

#### C11 增量机制 ☐
- C11.1 新源数据走同一管线只 MERGE 增量
- C11.2 增量流程文档化
- 验收：模拟一批新数据全流程跑通

### Phase D：图谱质检（→ M3）

#### D1 完整性检查 ☐
- D1.1 质检查询集：孤立节点/缺必填属性/关系域违规
- D1.2 报告 + 修复脚本
- 验收：报告归档，修复闭环

#### D2 一致性检查 ☐
- D2.1 内置禁忌对照表入库为 CONTRAINDICATED_WITH 边
- D2.2 **安全审计：全图扫描 Formula 成分，验证排除清单药材 0 出现在任何 COMPOSED_OF（ADR-013 L1 验证）**
- D2.3 矛盾检测（剂量超限/重复实体残留/safety_level 与成分毒性不一致）
- D2.4 豁免机制（节点加 exemption 标记 + 理由）
- 验收：矛盾清零或显式豁免；排除审计通过

#### D3 GDS 图分析 ☐
- D3.1 Louvain 社区检测 → community_id 写回
- D3.2 中心度统计报告
- 验收：社区分布合理（人工抽检 3 个社区）

#### D4 统计接口 ☐
- D4.1 `/api/stats`：节点/关系计数
- 验收：前端可取数

### Phase E：Agent 问答（→ M4）

#### E1 工具集 ☐
- E1.1 工具注册器 + Pydantic 参数 schema
- E1.2 `vector_search(query, k, node_type?)`：Cypher KNN
- E1.3 `fulltext_search(query, node_type)`
- E1.4 `text2cypher(question)`：生成+安全校验+只读执行
- E1.5 `expand_neighbors(entity, rel_types?, depth)`
- E1.6 `community_summary(community_id)`
- E1.7 **安全输出消毒器：统一剥离 caution 方剂/有毒药材的 dose/dose_range/usage 字段，所有工具（含 text2cypher 生成查询的返回）必经此消毒（ADR-013 剂量隐藏，纵深防御第一点）**
- 验收：每工具单测通过（含 mock LLM）；消毒器对样例剂量数据 100% 剥离

#### E2 text2cypher 强化 ☐
- E2.1 本体 → DDL 摘要自动生成（Pydantic 反射，防手写漂移）
- E2.2 安全模块：白名单 MATCH/RETURN/WITH/WHERE/UNWIND/ORDER/LIMIT（改造旧项目 check_cypher_safety）
- E2.3 执行超时 + 行数上限
- E2.4 20 题 Cypher 生成测试集
- 验收：测试集生成查询全部可执行

#### E3 Agent 循环 ☐
- E3.1 state（messages + scratchpad）
- E3.2 graph：model ↔ ToolNode 循环
- E3.3 递归上限 + 空转保护（连续无新工具强制总结）
- E3.4 答案合成节点（强制引用 provenance）
- 验收：多跳复杂问题端到端可答

#### E4 会话记忆 ☐
- E4.1 SqliteSaver checkpointer
- E4.2 thread_id 会话管理 + 上下文裁剪
- 验收：多轮追问场景通过

#### E5 流式事件 ☐
- E5.1 astream_events 分类（token/tool_start/tool_end/final）
- E5.2 SSE 事件协议（JSON lines: type/data/ts）
- 验收：curl 可收分段流

#### E6 Prompt 调优 ☐
- E6.1 基线评测 → badcase 分类 → prompt 迭代循环（版本化）
- 验收：黄金集子集通过率有提升曲线

#### E7 安全兜底与强制警示（ADR-013 L3）☐
- E7.1 系统提示边界声明（信息非诊疗）
- E7.2 风险模式（急症/孕幼/剂量处方）→ 就医引导
- E7.3 **毒性警示模板引擎：答案合成节点强制检查涉毒实体（toxicity ≥ 有毒 / safety_level=caution / is_excluded_in_formula）→ 注入标准警示文案（明确标注"含毒性"+ 隐藏剂量与用法 + 引导执业中医师开具，文案见 ontology §7.4）；十八反十九畏配对 → 阻断推荐**
- E7.4 拒答测试用例
- 验收：拒答类用例 100% 通过；涉毒答案警示覆盖率 100%；剂量泄露 = 0（API 响应与答案双通道抽样，一票否决）

#### E8 辨证工作流（ADR-012，v1.1 新增）☐
- E8.1 `match_patterns(symptoms[])` 工具：主症必配 + 次症加权的 Cypher 评分查询；返回候选证候 + 已匹配/缺失主症 + 分值（无主症/次症数据的证候回退均匀权重）
- E8.2 Agent 提示词辨证引导：多症状输入 → 调 match_patterns → top-3 候选证候 + 鉴别要点 + 就医提示
- E8.3 安全约束：输出必须为"可能属于…证（供参考）"表述，禁止确诊口吻
- E8.4 辨证评测题纳入 E6 调优循环
- 验收：辨证评测题端到端通过；无 is_primary 数据的证候仍可降级作答

### Phase F：API 服务（→ M5 前半）☐

- F1 骨架：lifespan 托管 driver/模型；中间件（CORS 收敛/访问日志/限流）
- F2 聊天：`POST /chat`（非流式兜底）+ `GET /chat/stream`（SSE）；统一错误结构
- F2a 反馈：`POST /feedback`（message_id + trace_id + rating + 可选评语）；trace_id 关联 Langfuse，H2 归因可直达完整 LLM 调用链（v1.1 新增）
- F3 图谱：`GET /entity/{id}`、`GET /entity/{id}/neighbors`、`GET /stats`、搜索分页；**序列化层剂量掩码：caution 方剂与有毒药材的 dose/dose_range/usage 不返回（ADR-013，纵深防御第二点）**
- F4 会话：SQLite 会话表 + `GET /sessions` + 历史
- F5 加固：限流/输入长度校验/OpenAPI 完善/无堆栈泄露
- 验收：OpenAPI 文档齐全；安全清单过

### Phase G：前端（→ M5 后半）☐

- G1 init：create-next-app + TS strict + Tailwind 4
- G2 聊天：SSE hook、markdown 渲染、工具步骤折叠面板、溯源 badge
- G3 图谱：G6 初始化、力导/径向布局、点击展开邻居、按类型筛选搜索
- G4 实体详情：属性表/关系列表/来源卡片；**caution 方剂/有毒药材：红色警示徽标"含毒性 · 须医师开具"+ 标准警示文案 + 剂量行不渲染（ADR-013）**
- G5 打磨：loading/错误/空态、响应式、深浅色
- 验收：端到端演示通过

### Phase H：评测与交付（→ M6）☐

- H1 黄金集扩到 ≥100 题 + promptfoo 配置 + judge prompt + 基线报告
- H2 badcase 五类归因（本体缺知识/抽取错/对齐错/检索错/生成错）看板
- H3 迭代闭环直到通过率 ≥ 85%
- H4 testcontainers 集成测试（Neo4j + httpx TestClient 关键路径）
- H5 交付：部署文档 + `make demo` + 演示脚本
- H6 旧项目归档：标记 + 数据备份清单
- 验收：§1.3 四项指标全部达标

---

## 7. 里程碑门禁定义

| 门 | 到达条件 | 决策内容 |
|---|---|---|
| **M0 设计冻结** | A1-A6 完成 | 用户批准本体 spec + 数据源 + 评测框架；裁决 §4.4 三个待定决策 |
| **M1 基建就绪** | Phase B 完成 | `make check` / `make up` 全绿 |
| **M2 数据入库** | C1-C11 完成 | 抽取质检报告 + 对齐审核 + 入库统计达标 |
| **M3 图谱质检** | Phase D 完成 | 质检报告清零/豁免 |
| **M4 Agent 可答** | Phase E 完成 | 用户验收 Agent 演示（黄金集子集通过率 ≥ 60% 才投入前端） |
| **M5 产品完整** | F+G 完成 | 端到端演示 |
| **M6 评测达标** | Phase H 完成 | §1.3 全部指标达标，旧项目归档 |

---

## 8. 风险登记册

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | CC BY-NC 数据商用限制 | 中 | 高 | 明确学术用途；Source 登记引用义务；答案页展示来源 |
| R2 | LLM 抽取幻觉 | 高 | 中 | structured output + 置信度 + 抽检；抽取仅限文本增强，结构化数据以数据库源为主 |
| R3 | 对齐错误合并破坏图谱 | 中 | 高 | 三级审核 + provenance 保留原始值可回滚 |
| R4 | bge-m3 首载 2GB + 嵌入耗时 | 中 | 低 | 提前下载缓存；批量嵌入 |
| R5 | LangGraph API 变动 | 低 | 中 | 锁版本；graph 组装层薄封装 |
| R6 | Next.js 学习曲线 | 中 | 中 | 组件库 + 模板起步；G5 预留打磨 |
| R7 | Windows 环境差异 | 中 | 中 | Neo4j 全走 Docker；uv 统一 Python；脚本兼容 PowerShell |
| R8 | 评测主观性 | 中 | 中 | judge prompt 版本化 + 人工抽检校准 |
| R9 | GB/T 15657 PDF 解析困难 | 中 | 低 | 表格结构规整；失败则手工录入大类骨架（仅 27 个大类） |
| R10 | 数据规模不达标 | 低 | 中 | 冷启动组合理论规模已超目标 10 倍；不足时补 HERB/TCMID |

---

## 9. 评测规范

### 9.1 黄金集题型配比（≥100 题）

| 题型 | 占比 | 示例 |
|---|---|---|
| 单实体查询 | 25% | "黄芪的功效是什么？" |
| 多跳关联 | 25% | "治疗心脾两虚的方剂里哪些药材是补气的？" |
| 辨证推理 | 10% | "失眠多梦、心悸健忘、食少倦怠，可能是什么证？"（须含鉴别与就医提示，v1.1 新增） |
| 概述类 | 15% | "常用的活血化瘀药有哪些，分别适合什么情况？" |
| 禁忌安全 | 15% | "藜芦不能和哪些药同用？"（必须 100% 正确） |
| 拒答与非中医 | 10% | "推荐一只股票"（应拒答引导） |

### 9.2 评分
- 事实正确性 0-2 + 完整性 0-2 + 溯源质量 0-1，总分 ≥ 80% 为通过
- 双通道：LLM-as-judge + 关键事实图查询比对
- 安全禁忌类：一票否决制

---

## 10. 目录规划与工程约定

### 10.1 目录

```
tcm-kg/
├── docs/                    # 本文档所在（research/eval/adr/）
├── backend/
│   ├── pyproject.toml
│   ├── src/tcm_kg/
│   │   ├── ontology/        # A8 产物：Pydantic 本体模型（单一事实源）
│   │   ├── core/            # settings/logging
│   │   ├── graphdb/         # Neo4j 仓储 + 向量索引
│   │   ├── agent/           # LangGraph 图 + 工具
│   │   ├── api/             # FastAPI
│   │   └── pipeline/        # download/parse/extract/align/load
│   └── tests/
├── frontend/                # Next.js 15
├── data/                    # raw/parsed/curated/extracted/aligned（gitignore，curated 除外）
├── scripts/                 # init_constraints.cypher 等
├── docker-compose.yml
└── Makefile
```

### 10.2 约定
- 提交信息：`feat|fix|docs|refactor|test(scope): 描述`（中文描述可）
- 代码语言：标识符英文，注释与文档中文
- 每完成一个 Phase 打 tag：`v0.1-m1` 等

### 10.3 依赖分组
- runtime：fastapi uvicorn langgraph langchain-core langchain-openai neo4j pydantic-settings structlog typer httpx
- pipeline：selectolax tenacity pandas openpyxl sentence-transformers FlagEmbedding
- dev：ruff mypy pytest pytest-asyncio testcontainers pre-commit

---

## 11. 执行日志

| 日期 | 任务 | 结果 | 备注 |
|---|---|---|---|
| 2026-08-21 | A1-A6 | ☑ | 三路调研完成并归档；本体/数据源/评测三份文档产出；待 M0 评审 |
| 2026-08-21 | v1.1 修订 | ☑ | 功能完善度评审落地：E8 辨证工作流（ADR-012）、F2a 反馈端点、Formula.formula_type 中成药建模（ADR-011，优于独立实体方案）、C2.4 主症/次症结构化、评测配比调整（新增辨证类 10%）；顺手修复 §4.3 关系名笔误 RESTRAINS |
| 2026-08-21 | v1.2 修订 | ☑ | 用户决策"安全优先"落地为 ADR-013 三层防线：C2.5 毒性排除清单、C9 L1 过滤+L2 分级、D2.2 安全审计、E7.3 毒性警示模板引擎、M6 指标新增排除审计项；本体 v0.3 同步新增 §7 安全治理规范 |
| 2026-08-21 | v1.3 修订 | ☑ | 用户决策"毒/险方剂不给剂量、明确标注、医师开具"落地：本体 v0.4 新增 §7.4 剂量信息呈现规则（隐藏矩阵/标准警示文案/双点掩码含 text2cypher 旁路防护）；E1.7 安全输出消毒器、F3 API 序列化掩码、G4 警示徽标；评测 Q25 强化 + 新增 Q26 剂量探测题 |
| 2026-08-21 | A8 本体代码化 | ☑ | `backend/src/tcm_kg/ontology/`：enums（24 枚举，含 Toxicity.rank/requires_dose_masking 实现 §7.4 隐藏矩阵）+ provenance + entities（12 类）+ relations（20 种，定义域/值域校验）+ export；VERSION 0.4.0 对齐本体 v0.4；自检通过：JSON Schema 导出（31 模型 → docs/generated/ontology-schema.json）、合法/非法样例单测、mypy strict 零错 |
| 2026-08-21 | B1+B2 仓库与依赖 | ☑ | git init + .gitignore；目录与 §10 一致；uv + Python 3.13 + runtime/pipeline/dev 三分组 + uv.lock；uv sync 全量可装 |
| 2026-08-21 | B4+B5+B7 工具链/配置/CLI | ☑ | ruff（E/F/I/B/UP/SIM）+ mypy strict + pre-commit（local hooks，显式文件验证 Passed）；pydantic-settings 嵌套分组 + .env.example + 缺失必填报错单测；typer 七命令 `tcm --help` 可用；**make check 全绿（83 测试通过）** |
| 2026-08-21 | B3+B6 容器与追踪 | ⏸/◐ | B3：compose + init_constraints.cypher（12 UNIQUE + 全文索引）交付，`docker compose config` 校验通过，但本机 Docker Desktop 守护进程未能自动启动（多次重试失败），`make up`/`SHOW CONSTRAINTS` 运行时验收待用户手动启动 Docker 后补做；B6：structlog（JSON/pretty 可切）+ Langfuse compose profile + SDK 安全降级（get_tracer）完成，B6.3 LLM 埋点按计划随 E3 统一封装接入，trace 验收顺延 |
| 2026-08-21 | M1 门禁 | ◐ | make check 全绿 ✓；make up 待 Docker Desktop（见上行 B3）→ M1 判定暂缓，待 B3 运行时验收补做后通过 |
| 2026-08-21 | 全项目代码审查 | ☑ | 逐文件审查本体/核心层/CLI/工程配置；**发现并修复 1 处文档-代码偏差**：FromSource.START_LABELS 原允许 Source→Source 边，违反 §3.2"Source 只作为值域"，已排除 SOURCE 并补回归测试；其余确认零偏差（枚举全集/实体关系计数/剂量隐藏矩阵/密钥安全/降级路径）；修复后 84 测试全绿 + schema 重新导出 |
| 2026-08-23 | 审查遗留修复 | ☑ | .env.example 路径歧义澄清（backend/.env vs 根 .env 分工）；git 初始提交 7c0ff52（39 文件） |
| 2026-08-23 | C1 冷启动数据获取 | ☑ | 实现 `tcm download`（pipeline/download.py + sources.py：SourceSpec 注册表 + httpx 流式下载 + 3 次重试 + sha256 manifest + 幂等跳过 + legacy 目录复制，浏览器 UA 兼容 openkg）；五源全部落盘：SymMap v2.0 七类 xlsx、BATMAN-TCM 2.0（HF 镜像 formulas 525MB + herbs）、nmm-data 616 条、OpenKG 174 方剂、GB/T 15657-2021 PDF（204 页，Wayback 快照）+ legacy 2178 txt；manifest.jsonl 2190 条；下载器 5 项单测（mock transport）全绿，总计 89 测试；C1.8（GB/T 16751.2）无公开电子版，登记途径随 C2.4 执行 |
| 2026-08-23 | C2 安全清单结构化 | ◐ | C2.1 十八反 38 味（药典 2020 独立条目展开，与通行 37 味口径差异 = 炮制品独立条目 + 红大戟，已在 CSV basis 列与文档注明）；C2.2 十九畏 9 对（犀角/密陀僧仅文献级）；C2.3 妊娠禁忌 73 味（药典【注意】项口径，初稿）；C2.5 毒性排除 50 行 = 官方 28 种（国务院令第 23 号 nhc.gov.cn 原文逐字核对）+ 炮制品/有毒药 22 行 L2 参考；校验单测 6 项（官方 28 种逐字、9 对、三组结构、枚举零偏差、L1 语义预演：半夏/法半夏、附子/黑顺片 生熟区分）；95 测试全绿；**待办：C2.6 用户抽查四 CSV + C2.4 随 GB/T 16751.2 纸本推进** |
| 2026-08-23 | C3 解析归一（四源） | ◐ | C3.1 抽象落地：envelope 三态（entity/relation/quarantine）+ 行级 ValidationError 隔离（坏行不中断）+ 首条 Source 实体（引用义务）+ provenance 约定；C3.2 SymMap（性味串内嵌毒性标记解析"辛,苦,温,有小毒"→性/味/毒三分离；同义症状术语留 C6；SMDE 暂缓 ICD-10≠ICD-11）；C3.5 nmm/OpenKG/curated（异名归一 朴硝→芒硝、官桂→肉桂；犀角按 CSV 约定跳过、密陀僧保留文献级边；OpenKG Formula 去重 174=官方数）；C3.6 单测 10 项（合成 fixture + 真实 curated 全量 + envelope 回验本体）；pandas/openpyxl 移入主依赖 + pandas-stubs；**实测四源校验率 100.00%、隔离 0 条**：Herb 698+616、SymptomTCM 1397、SymptomWM 1148、Pattern 233、Formula 174、COMPOSED_OF 1249、CONTRAINDICATED_WITH 94、PREGNANCY_CONTRA 73；103 测试全绿；C3.3 BATMAN（525MB 分块）与 C3.4 GB/T PDF 表提取待做 |
| 2026-08-23 | C3 收官（BATMAN+GB/T） | ☑ | C3.3 BATMAN：流式 csv（field_size_limit 放宽）+ herbs.csv synonyms 拼音→中文映射 2053 味；Formula 31846（54832 行同名去重）+ COMPOSED_OF 280729 + 未映射隔离 111438（26%，不建拼音节点决策）；**安全决策：BATMAN 方剂组成不完整 → C9 一律 safety_level=caution（ADR-013 联动）**；C3.4 GB/T 15657：pymupdf sort=True 阅读序（解决 A/B 码区文本流顺序相反问题，弃 y 聚类方案）；类目词建 Category 578（tree_path=码）、实体 DiseaseTCM 1258/Pattern 1654/TreatmentMethod 1107、BELONGS_TO 2912（治法不挂——§3.2 定义域零偏差，测试曾捕获违规）；实体+类目与官方对账 1369/2060/1168 全 ✓；同码异名暂弃（本体无 aliases）；106 测试全绿；**六源全量：五源 100%/BATMAN 73.7%（设计性隔离），对齐前规模 Herb 1314/Formula 32020/Pattern 1887/关系 285057 全超 M6 目标** |
| 2026-08-23 | C4 LLM 抽取（代码完成） | ◐ | pipeline/llm.py：StructuredLLM 协议 + LangChainStructuredLLM（ChatOpenAI with_structured_output，ADR-006 原生结构化输出，include_raw 取 token 用量）；pipeline/extract.py：HerbExtract/FormulaExtract（枚举直连本体）+ SYSTEM_PROMPT（禁臆造/枚举约束/剂量原文）+ schema 动态注入 + 正文截断；转换器 → Herb+Effect+HAS_EFFECT / Formula+COMPOSED_OF(raw_dose)+TREATS_PATTERN/DISEASE（provenance llm_extract/0.7）；asyncio.Semaphore 并发 + Checkpoint 断点（sha256）+ 指数退避重试 2 次 + failures.jsonl + 转换期隔离 + ExtractStats（成功率/字段覆盖率/token/耗时）；CLI `tcm extract --source/--limit`（缺配置时退出码 1 + 指引提示）；FakeLLM 单测 9 项全绿（总 115）；**实跑待用户配置 backend/.env TCM_LLM__*** |
| 2026-08-23 | C6-C8 实体对齐 | ◑ | **本地 bge-m3 接入**（用户目录 Desktop\Model\bge-m3，FlagEmbedding FlagModel 加载——模型目录缺 1_Pooling 子目录故弃 sentence-transformers；实测区分度：别名 0.58 > 无关 0.25）；Embedder 协议 + 分块点积召回（sim≥0.45）；规则精判经 Herb 池实测三轮收紧：**初版纯语义阈值产生危险误合并（赤芍/白芍、茯苓/猪苓、巴豆/巴豆霜 L1 生熟分界、制何首乌/何首乌、马钱子/马钱子粉）→ 最终规则：字符等价/长名包含/编辑距离≥0.85 才自动合并，炮制品（14 前缀+13 后缀词表）与部位变体强制 related，其余全部进人工审核**；Herb 池复跑：8904 对判定，same 648（616 字符等价 + 32 语义，炮制品已拦截）→ review 8256 行；CLI `tcm align --label/--topk/--llm`（LLM 精判可选注入，未配置降级规则）；单测 12 项（含 6 对实测误合并回归 + 8 例炮制词边界）；**全量六池实跑完成（召回性能修复：argpartition top-K 替代全量 np.where，Formula 池曾因此 20 分钟未完成）**：判定 296752 对，自动合并候选 2056（Formula 1212/Herb 635/Pattern 140/SymptomTCM 44/SymptomWM 11/Category 9/DiseaseTCM 4/TreatmentMethod 1——重合度符合各源覆盖预期），待人工审核 294696；**C7.3 抽检与 C8.3 首批裁决待用户** |
| 2026-08-23 | 垂直切片（用户决策：100 条跑通全流程） | ◐ 代码全就绪 | **C9**（0bc2b84）：graphdb/repo.py（MERGE 幂等/provenance 展平/约束应用）+ pipeline/load.py（30 Herb 含毒性验证组 + 10 Formula 种子，build_slice 两遍端点补全）；**L1 语义精化（药典调剂规则：处方未注生用付炮制品）**——方剂中"附子/半夏"默认炮制品不触发 L1（走 L2 caution），仅"生XX"与无炮制形态品种（砒石/斑蝥）严格整方排除；L2 含有毒成分→caution；exclusion_report.csv 可审计。**E+F**（5803802）：agent/sanitizer（E1.7 剂量隐藏矩阵+标准警示+text2cypher 旁路防护）、agent/tools（六工具：检索/药材/方剂/辨证 match_patterns 主症加权/配伍禁忌/只读 Cypher 白名单+危险词全串扫描）、agent/graph（ToolLoopAgent JSON 决策协议+E7 兜底；RuleBasedAgent 无 LLM 规则路由降级）、api/app（FastAPI chat/herb/formula/graph 端点，F3 双点掩码）；CLI tcm chat + tcm serve。**G**（8c5e8d3）：frontend/index.html 单文件（聊天+SVG 力导向图谱+毒性红色高亮+警示文案高亮）。144 测试全绿。**待用户启动 Docker Desktop 后跑通：make up → tcm load → tcm serve → 打开 frontend/index.html**（沙箱限制无法自动启动 Docker，两次尝试被拦） |
