# 本草通 2.0 · 中医知识图谱 — 面试准备文档

> 项目路径：`tcm-kg/` | 技术栈：Python 3.13 + Neo4j 2025 + LangGraph + FastAPI
> 当前状态：100 条精选数据垂直切片全流程跑通，156 单测通过

---

## 一、项目概述（30 秒电梯陈述）

本草通 2.0 是一个**安全优先的中医知识图谱 + Agentic GraphRAG 问答系统**。

核心能力：将多源中医药数据（国标、药典、数据库）融合为知识图谱，通过 Agent 六工具提供药材查询、方剂分析、辨证推理、配伍禁忌检查，**对有毒方剂自动隐藏剂量并引导就医**。

技术亮点：12 类实体 20 种关系的本体设计、三层冗余安全防线、主症必配+次症加权的辨证推理、双点掩码的剂量保护机制。

---

## 二、技术栈选型与理由

| 层 | 技术 | 版本 | 选型理由 |
|---|---|---|---|
| 语言 | Python | 3.13 | 性能提升（PEG 解析器、自由线程），StrEnum 原生支持 |
| 包管理 | uv | 最新 | 比 pip/poetry 快 10-100 倍，lockfile 确定性构建 |
| 图数据库 | Neo4j | 2025.01 社区版 | 图+向量一库（HNSW），Cypher 声明式查询，APOC+GDS 插件 |
| Agent 框架 | LangGraph | 1.x | tool-loop 薄封装，可控循环，比 ReAct 更结构化 |
| Web 框架 | FastAPI | 0.115+ | 异步、类型安全、自动 OpenAPI 文档 |
| 数据建模 | Pydantic | 2.10+ | V2 性能 10x，strict 模式，JSON Schema 导出 |
| CLI | Typer | 0.15+ | 类型驱动 CLI，自动补全 |
| 嵌入模型 | bge-m3 | 本地 | 多语言、8192 token 上下文，本地部署零成本 |
| 质量工具 | ruff + mypy strict | 最新 | 零 lint、严格类型检查 |
| 可观测 | structlog + Langfuse | v2 | 结构化日志 + LLM trace 追踪 |

**关键 ADR（架构决策记录）：**

- **ADR-002**：Agent 采用薄封装而非重框架——LangGraph tool-loop 语义自实现，控制力强
- **ADR-003**：图+向量一库——Neo4j 2025 HNSW 原生支持，避免 Pinecone+Neo4j 双库同步复杂度
- **ADR-011**：中成药用 `Formula.formula_type` 属性建模，不建独立实体——复用关系，避免同源拆分
- **ADR-012**：辨证用主症必配+次症加权 Cypher——可解释、可调试，不依赖黑盒 LLM
- **ADR-013**：三层安全防线 + 双点掩码——冗余设计，单点失效不泄露剂量

---

## 三、系统架构（五层垂直切片）

```
┌─────────────────────────────────────────────────────┐
│  L1 · 数据源（5 源 ~33 万行）                        │
│  GB/T 15657-2021 · BATMAN-TCM · NMM药典 · SymMap   │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  L2 · 数据管线（tcm CLI 四阶段）                     │
│  download → parse → align → load（安全过滤嵌入）     │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  L3 · 图数据库 Neo4j 2025（核心枢纽）                │
│  12 类实体 · 20 种关系 · 12 个 UNIQUE 约束          │
│  HNSW 向量索引（C10 阶段）· APOC + GDS 插件         │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  L4 · 服务层                                         │
│  Agent 六工具（规则/LLM 双模）· FastAPI 5 端点       │
│  F3 双点掩码 · E7 兜底警示                          │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  L5 · 前端                                           │
│  单文件 HTML（SVG 图谱 + 聊天）→ Next.js 15 规划    │
└─────────────────────────────────────────────────────┘
```

**垂直切片策略**：100 条精选数据（30 药材 + 10 方剂 + 10 证候 + 20 症状 + 关联边）跑通全部五层，验证端到端可行性后再放量。

---

## 四、本体设计（知识图谱建模）

### 4.1 实体模型（12 类）

| 实体 | 标签 | 数量(切片) | 核心属性 | 来源标准 |
|---|---|---|---|---|
| 药材 | Herb | 70 | name, nature, flavors, meridians, toxicity, dose_range | 药典 |
| 方剂 | Formula | 10 | name, formula_type, safety_level, source_book, usage | 方剂学 |
| 证候 | Pattern | 10 | name, category | GB/T 16751.2 |
| 症状 | SymptomTCM | 20 | name, description | GB/T 16751.2 |
| 功效 | Effect | — | name | 药典 |
| 疾病 | DiseaseTCM | — | name | ICD-11 TM1 |
| 治法 | TreatmentMethod | — | name | 方剂学 |
| 药材分类 | Category | — | name | GB/T 15657 |
| 妊娠禁忌 | PregnancyContra | — | name, level | 药典注意项 |
| 来源 | Source | 5 | name, source_type, url | — |
| 文献 | Book | — | name, dynasty, author | — |
| 临时术语 | ProvisionalTerm | — | name, review_status | — |

### 4.2 关系模型（20 种，核心 6 种）

| 关系 | 方向 | 语义 | 示例 |
|---|---|---|---|
| COMPOSED_OF | Formula → Herb | 方剂组成（含君臣佐使角色+剂量） | 四逆汤-[附子(君)]-> |
| TREATS | Formula → Pattern | 方剂主治证候 | 四逆汤->[阳虚证] |
| HAS_SYMPTOM | Pattern → SymptomTCM | 证候表现（主症/次症加权） | 阴虚证->[心悸(主,2分)] |
| HAS_EFFECT | Herb → Effect | 药材功效 | 附子->[回阳救逆] |
| CONTRAINDICATED_WITH | Herb ↔ Herb | 配伍禁忌（十八反/十九畏） | 甘草<->[甘遂](十八反) |
| BELONGS_TO | Entity → Category | 实体归类 | 阴虚证->[脏腑证] |

### 4.3 枚举体系（11 类枚举）

```
Nature(药性8级)  Flavor(药味7种)  Meridian(归经12经)
Toxicity(毒性4级: 无毒→小毒→有毒→大毒，含 rank + requires_dose_masking)
SafetyLevel(安全级: safe/caution — 不存在 high_risk，生品在 L1 即排除)
FormulaRole(君臣佐使)  FormulaType(经方/时方/中成药)
DosageForm(丸散膏丹...)  ContraType(十八反/十九畏)
PatternCategory(8类证候)  ProvenanceMethod(curated/db_import/llm_extract/manual_review)
```

**Toxicity 枚举的设计亮点**：内嵌 `rank` 属性（0-3 严重度序）和 `requires_dose_masking` 属性（rank ≥ 2 即 True），将安全判定逻辑封装在枚举内，消除散落的 if-else。

---

## 五、三层安全防线（核心亮点）

这是本项目最核心的设计——**安全优先，毒性方剂不给剂量，引导就医**。

```
输入：附子（有毒·生品）
  │
  ▼
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  L1 · 数据过滤    │────▶│  L2 · 风险分级    │────▶│  L3 · 答案消毒    │
│                  │     │                  │     │                  │
│  生品毒药 → 排除  │     │  含毒成分 → caution│     │  caution → 剂量掩码│
│  无炮制品种 → 排除│     │  BATMAN源 → caution│     │  有毒药材 → 剂量掩码│
│                  │     │                  │     │                  │
│  国务院令第23号   │     │  safety_level 字段│     │  MASK + 警示文案  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
                                                         │
                                                         ▼
                                    ⚠ 该方剂含毒性成分（附子）。
                                    剂量信息已隐藏。
                                    请前往正规医疗机构，
                                    由执业中医师辨证后开具处方使用。
```

### L1 数据层过滤（入库时）

- **依据**：国务院令第23号（医疗用毒性药品管理办法）
- **规则**：生品毒药（如生附子、生川乌）和无炮制形态品种 → 方剂整方排除，不入库
- **实现**：`toxic_exclusion.csv`（50 行毒性药材清单）+ `l1_strict_names()` 函数
- **排除报告**：生成 CSV 含被排除方剂、命中毒药材、排除原因

### L2 风险分级（入库时）

- **规则**：方剂含 `有毒`/`大毒` 药材 → `safety_level = caution`；BATMAN 源方剂（组成不完整）→ caution；其余 → safe
- **设计决策**：不存在 `high_risk` 级别——含生品毒药的方剂在 L1 即被排除，根本不入库

### L3 答案消毒（查询时，双点掩码）

- **第一点**：Agent 工具层（`sanitizer.py`）——药材 `dose_range` 掩码、方剂 `usage` 掩码、组成中毒药材 `dose`/`raw_dose` 掩码
- **第二点**：API 层（`app.py`）——出口再过一次 `sanitize_herb`/`sanitize_formula`，即使工具层遗漏也不泄露
- **兜底**：`E7 兜底`——Agent 最终答案含 MASK 但缺警示 → 自动追加标准警示
- **旁路防护**：`run_cypher` 工具——只读白名单校验（禁 CREATE/DELETE/SET 等）+ 输出行兜底掩码

**剂量隐藏矩阵**：

| 对象 | 判定条件 | 处理 |
|---|---|---|
| 方剂 | safety_level = caution | usage + 组成剂量掩码 |
| 药材 | toxicity ∈ {有毒, 大毒} | dose_range 掩码 |
| 药材 | is_excluded_in_formula | 同上 + 不给使用建议 |
| 其余 | — | 正常展示 |

**标准警示文案**（不可篡改，模板化）：

```
⚠ 该{方剂/药材}含毒性成分（{命中毒药材列表}）。出于安全考虑，此处不提供
剂量与用法信息。请前往正规医疗机构，由执业中医师辨证后开具处方使用。
```

---

## 六、数据管线（四阶段）

```
① download → ② parse → ③ align → ④ load(安全过滤)
   原始拉取     信封格式    实体对齐     入库+治理
```

### 6.1 信封格式（统一数据模型）

所有源数据解析为统一的 JSONL envelope：

```json
{
  "kind": "entity",          // entity | relation
  "label": "Herb",           // 实体标签
  "record": {
    "name": "附子",
    "nature": "大热",
    "toxicity": "有毒",
    "provenance": {           // 溯源（所有实体必带）
      "source_id": "src-symmap",
      "confidence": 0.95,
      "extracted_at": "2026-08-24T00:00:00Z",
      "method": "db_import"
    }
  }
}
```

### 6.2 多源融合策略

| 源 | 内容 | 优先级 | 特点 |
|---|---|---|---|
| src-curator-v0 | 人工整理种子 | 0（最高） | 权威兜底 |
| src-openkg | 方剂组成 | 1 | 组成干净 |
| src-nmm | 药典药材 | 2 | 性味归经 |
| src-symmap | 药材-症状映射 | 2 | 毒性字段 |
| src-gbt15657 | 国标分类 | 2 | 证候归类 |
| src-batman | 药材-靶点 | 9（最低） | 组成混杂，仅参考 |

**源优先级注册 + 同级字段合并**：同名单标签实体按优先级覆盖；同级源（如 nmm 和 symmap）做字段级合并——nmm 缺毒性字段由 symmap 补齐，symmap 缺拉丁名由 nmm 补齐。

### 6.3 入库终检

边的两端实体的 **name + label** 都必须在注册表中一致——跨源同名异标（如 gbt15657 视"便秘"为病、symmap 视为症）的边会被丢弃。

### 6.4 溯源追踪

所有实体和关系携带 `provenance`：`source_id` + `method` + `confidence` + `extracted_at`，支持多源冲突时按 confidence 排序展示。

---

## 七、Agent 六工具与辨证推理

### 7.1 Agent 架构（双模式）

**LLM 模式（ToolLoopAgent）**：
- LangGraph 语义的 tool-loop 薄封装
- LLM 输出 JSON `{"tool": name, "args": {...}}` 调工具，或 `{"answer": text}` 直接作答
- 最多 8 步循环，系统 Prompt 强制安全规则
- E7 兜底：答案含 MASK 但缺警示 → 自动追加

**规则模式（RuleBasedAgent，无 LLM 降级）**：
- 关键词路由：辨证→match_patterns、禁忌→check_contraindications、方/汤/散/丸→get_formula、药/草→get_herb
- 引号提取：支持中文引号 `「」""''` 和书名号 `《》`
- 模板化输出：结构化文本含警示透传

### 7.2 六工具

| # | 工具 | 功能 | 安全处理 |
|---|---|---|---|
| 1 | search_entities(name) | 全文/别名检索 | 返回含毒性与安全级标记 |
| 2 | get_herb(name) | 药材详情 | → sanitize_herb（毒药剂量掩码）|
| 3 | get_formula(name) | 方剂详情+组成 | → sanitize_formula（caution 剂量掩码）|
| 4 | match_patterns(symptoms) | 辨证推理 ★ | 无毒性数据暴露 |
| 5 | check_contraindications(herbs) | 配伍禁忌 | 返回十八反/十九畏/妊娠禁忌 |
| 6 | run_cypher(query) | 只读 Cypher | 白名单校验 + 兜底消毒 |

### 7.3 辨证推理（核心算法）

**ADR-012：主症必配 + 次症加权的 Cypher 评分查询**

```cypher
UNWIND $symptoms AS s
MATCH (p:Pattern)-[hs:HAS_SYMPTOM]->(st:SymptomTCM {name: s})
WITH p,
     sum(CASE WHEN hs.is_primary THEN 2 ELSE 1 END) AS score,  -- 主症 2 分，次症 1 分
     collect(CASE WHEN hs.is_primary THEN st.name END) AS primary_hits,
     collect(CASE WHEN hs.is_primary THEN null ELSE st.name END) AS secondary_hits
RETURN p.name AS pattern, score,
       [x IN primary_hits WHERE x IS NOT NULL] AS primary_hits,
       [x IN secondary_hits WHERE x IS NOT NULL] AS secondary_hits
ORDER BY score DESC LIMIT $topk
```

**设计要点**：
- 主症 2 分、次症 1 分——主症权重更高，避免次症堆砌导致误判
- 返回命中详情（主症命中/次症命中）——可解释、可调试
- 纯 Cypher 查询——不依赖 LLM 黑盒，结果确定性强

**示例**：输入"心悸、失眠、乏力" → 阴虚证（评分 4：心悸主症2+失眠主症2）

### 7.4 系统提示词安全规则

```
1. 优先调用工具获取事实，不要凭记忆编造中医药知识。
2. 工具结果中带 _safety_warning 字段时，必须在回答中原样包含该警示文案。
3. 涉及毒性药材/方剂剂量的问题：明确告知剂量信息出于安全考虑不予提供，引导就医。
4. 你提供的是知识查询服务，不构成医疗建议；回答末尾附一句就医提示。
5. 工具结果足够时立即作答，不要过度调用。
```

---

## 八、API 设计（5 端点）

| 端点 | 方法 | 功能 | 安全处理 |
|---|---|---|---|
| `/api/health` | GET | 健康检查 | — |
| `/api/chat` | POST | 问答（Agent） | safety_warning 透传 |
| `/api/herb/{name}` | GET | 药材详情 | 双点掩码（工具层+API层）|
| `/api/formula/{name}` | GET | 方剂详情 | 双点掩码 |
| `/api/graph` | GET | 图谱可视化数据 | 限量返回 |

**双点掩码设计**：即使工具层（第一点）遗漏消毒，API 层（第二点）出口再过一次 `sanitize_herb`/`sanitize_formula`，确保剂量不泄露。

**应用工厂模式**：`create_app(tools, agent)` 注入依赖，测试用 FakeRepo/FakeAgent，生产用 `create_default_app()` 自动构建。

---

## 九、工程质量保障

| 维度 | 工具 | 配置 | 结果 |
|---|---|---|---|
| 代码风格 | ruff | line-length=100, select=[E,F,I,B,UP,SIM] | 零违规 |
| 类型安全 | mypy | strict=true, warn_unreachable=true | 零错误 |
| 单元测试 | pytest | 156 个测试，覆盖消毒器/工具/Agent/API | 全通过 |
| 提交钩子 | pre-commit | ruff + mypy + pytest | 自动化 |
| 容器化 | Docker Compose | Neo4j + Langfuse profile 隔离 | 一键启动 |
| 模型校验 | Pydantic V2 | 12 实体 + 20 关系 strict 模型 | JSON Schema 导出 |

**测试覆盖关键路径**：
- 消毒器：剂量隐藏矩阵全部条件分支（无毒/有毒/大毒/caution/safe）
- 工具层：FakeRepo 脚本化查询验证六工具输出
- Agent：ToolLoopAgent（FakeChatLLM）+ RuleBasedAgent 路由
- 安全：L1 排除、L2 分级、L3 掩码、E7 兜底、旁路防护

---

## 十、项目难点与解决方案

### 难点 1：多源数据融合的实体冲突

**问题**：同一药材"附子"在 5 个源中属性不一致——nmm 无毒性字段、symmap 有、BATMAN 组成混杂。

**解决**：源优先级注册表（curator > openkg > nmm = symmap = gbt > batman）+ 同级源字段级合并。优先级覆盖低质量源，同级互补缺失字段。

### 难点 2：跨源同名异标

**问题**："便秘"在 gbt15657 是 `DiseaseTCM`、在 symmap 是 `SymptomTCM`，导致边的端点 label 不匹配，关系静默失败。

**解决**：入库终检——边两端实体的 name+label 都必须在注册表一致，不一致即丢弃。避免语义混淆。

### 难点 3：BATMAN 数据污染

**问题**：BATMAN-TCM 同名方剂混入大量无关组成（如"四逆汤"含 200+ 药材），字母序最先注册导致覆盖权威源。

**解决**：BATMAN 源优先级设为 9（最低），只参与 L2 caution 判定（组成不完整→caution），不参与 COMPOSED_OF 关系。

### 难点 4：安全防线静默失效

**问题**：`CURATED_DIR` 路径算错一级，毒性排除表加载 0 行，L1/L2 防线整体失效但无报错。

**解决**：修复路径 + 入库后验证 `SHOW CONSTRAINTS` 和图谱计数，确保 envelope 数 = 图谱数。

### 难点 5：Neo4j 关系属性不支持嵌套

**问题**：关系 record 中的 `start`/`end` 嵌套 dict 混入关系属性，Neo4j 报错但 `tx.run` 未 `consume`，异常被静默吞掉，89772 条关系全部失败。

**解决**：`_flatten_props()` 剥离 `name`/`start`/`end`/`provenance`，展平 `prov_*` 字段；`tx.run().consume()` 确保执行完成。

### 难点 6：辨证推理的准确性

**问题**：纯症状匹配导致次症堆砌误判（如所有含"乏力"的证候都高分）。

**解决**：主症必配 + 次症加权——主症 2 分次症 1 分，主症权重更高；返回命中详情供调试。

---

## 十一、当前数据指标

| 指标 | 值 |
|---|---|
| 入库实体 | 122（70 药材 + 10 方剂 + 10 证候 + 20 症状 + 基础设施）|
| 入库关系 | 176 |
| L1 排除方剂 | 0（切片数据已精选）|
| L2 caution 方剂 | 2（四逆汤含附子、归脾汤 BATMAN 源）|
| L2 safe 方剂 | 8 |
| 单元测试 | 156 个全通过 |
| 静态检查 | ruff 零违规 + mypy strict 零错误 |
| 图谱约束 | 12 个 UNIQUE 约束 |

---

## 十二、常见面试问题与回答要点

### Q1：为什么选 Neo4j 而不是 RDF/OWL？

- Cypher 声明式查询更适合路径发现和模式匹配（辨证推理就是模式匹配）
- 属性图模型天然支持在关系上挂属性（剂量、角色、权重）
- Neo4j 2025 HNSW 向量索引——图+向量一库，避免双库同步
- APOC + GDS 插件支持图算法（中心性、社区检测）
- RDF 三元组过于扁平，关系无法挂属性，需 reification

### Q2：三层安全防线为什么需要冗余？

- 单点防御不可靠——L1 依赖数据完整性，L2 依赖算法正确性，L3 依赖输出过滤
- 冗余设计确保**任一层失效，其他层仍能拦截**——L1 漏排时 L2/L3 兜底
- 双点掩码（工具层+API层）防 LLM 幻觉——即使 Agent 工具层遗漏，API 层再过一次
- 安全领域的设计原则：Defense in Depth

### Q3：辨证推理为什么不用 LLM？

- **可解释性**：Cypher 评分查询返回主症/次症命中详情，医生可验证
- **确定性**：同样输入永远同样输出，不依赖 LLM 随机性
- **安全性**：不依赖黑盒模型做医学推理，避免幻觉风险
- **成本**：纯数据库查询，零 token 消耗
- LLM 的角色是**调度工具+组织语言**，不是做医学推理

### Q4：Agent 的 tool-loop 和 ReAct 有什么区别？

- ReAct：Thought→Action→Observation 自由文本循环，依赖 LLM 解析能力
- Tool-loop：LLM 输出结构化 JSON `{"tool": name, "args": {...}}`，更可控
- 薄封装：不自建状态机，直接用 `for` 循环 + JSON 协议，代码量 < 100 行
- 最大步数限制（8 步）防止无限循环
- E7 兜底：最终答案安全检查，即使 LLM 不遵守 prompt 也能保安全

### Q5：数据溯源（provenance）为什么重要？

- 中医药知识有流派差异，多源冲突时需标注来源
- confidence 排序：curated(1.0) > db_import(0.95) > llm_extract(0.7)
- 用户可追溯每条知识的来源和可信度
- 监管合规：医疗知识需可追溯

### Q6：为什么用垂直切片（100 条）而不是全量？

- 快速验证端到端可行性——5 层全跑通再放量
- 100 条覆盖所有实体类型和关系类型，含毒性验证组
- 调试效率高——问题定位快，迭代快
- 全量入库前需先验证管线正确性，避免"垃圾进垃圾出"

### Q7：Pydantic V2 strict 模式的价值？

- 编译期类型检查 + 运行期数据校验双保险
- 12 实体模型约束字段类型和取值范围（如 Toxicity 枚举）
- JSON Schema 自动导出——可供前端/文档/API 契约使用
- 性能比 V1 快 10 倍

### Q8：如果 LLM 幻觉给出错误剂量怎么办？

- 三重防护：
  1. Agent 工具层：`sanitize_herb`/`sanitize_formula` 掩码
  2. API 层：出口再过一次 `sanitize_*`（双点掩码）
  3. E7 兜底：答案含 MASK 但缺警示 → 自动追加
- 系统提示词强制：`工具结果中带 _safety_warning 时必须原样包含`
- LLM 只能调度工具获取事实，不能凭记忆编造——prompt 规则 1

### Q9：项目的技术难点在哪里？

1. **多源融合**：5 个源数据格式不一、质量参差，需优先级+字段合并
2. **安全治理**：三层防线嵌在管线不同阶段，需保证一致性和不可绕过
3. **辨证准确性**：主症必配+次症加权的评分设计，需调优权重
4. **类型安全**：mypy strict + Pydantic strict，零类型错误
5. **性能**：100 条切片秒级返回，全量需向量索引优化

### Q10：后续规划？

| 阶段 | 内容 | 状态 |
|---|---|---|
| M1 垂直切片 | 100 条全流程 | ✅ 已完成 |
| C10 向量索引 | bge-m3 嵌入 + HNSW | 待执行 |
| 全量入库 | ~33 万条 | 待执行 |
| LLM 接入 | 本地/云端对话模型 | 待配置 |
| Next.js 前端 | AntV G6 正式可视化 | 待开发 |
| Langfuse trace | LLM 调用追踪 | 基础设施就绪 |
| 小红书模块 | 内容运营 | 暂缓 |

---

## 十三、关键代码位置索引

| 文件 | 行数 | 功能 |
|---|---|---|
| `backend/src/tcm_kg/ontology/enums.py` | 186 | 11 类枚举（含 Toxicity.rank/requires_dose_masking）|
| `backend/src/tcm_kg/ontology/entities.py` | — | 12 实体 Pydantic 模型 |
| `backend/src/tcm_kg/ontology/relations.py` | — | 20 关系 Pydantic 模型 |
| `backend/src/tcm_kg/pipeline/load.py` | — | 入库+L1/L2安全过滤+种子数据 |
| `backend/src/tcm_kg/agent/sanitizer.py` | 110 | L3 消毒器（剂量隐藏矩阵）|
| `backend/src/tcm_kg/agent/tools.py` | — | 六工具实现（含辨证Cypher）|
| `backend/src/tcm_kg/agent/graph.py` | 333 | ToolLoopAgent + RuleBasedAgent |
| `backend/src/tcm_kg/api/app.py` | 102 | FastAPI 5 端点 + 双点掩码 |
| `backend/src/tcm_kg/graphdb/repo.py` | — | Neo4j 仓储（属性展平+consume）|
| `backend/tests/agent/test_tools.py` | — | 消毒器+工具单测（FakeRepo）|
| `docker-compose.yml` | 73 | Neo4j + Langfuse 配置 |
| `data/curated/toxic_exclusion.csv` | 50 行 | 毒性药材排除清单 |

---

## 十四、一句话总结

> 本草通 2.0 是一个用 Python 3.13 + Neo4j 2025 + LangGraph 构建的中医知识图谱问答系统，核心创新是**三层安全防线（数据过滤→风险分级→答案消毒）确保有毒方剂剂量不泄露**，辨证推理采用**主症必配+次症加权的 Cypher 评分查询**保证可解释性，100 条精选数据已跑通从入库到前端的全流程，156 个单测 + ruff/mypy strict 零错误。
