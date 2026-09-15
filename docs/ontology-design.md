# 中医知识图谱本体设计规范 v0.4（待 M0 冻结）

> 单一事实源：本文档冻结后，`tcm_kg.ontology` Pydantic 模型必须与其零偏差；schema 变更先改本文档。
> 证据基础：`research/A1-standards-and-sources-survey.md`。

---

## 1. 设计原则

1. **标准对齐**：病证分类主骨架 GB/T 15657-2021；国际对齐层 ICD-11 TM1（`icd11_code` 字段）；术语规范名对齐《中医药学名词》与 2020 药典。
2. **证据可溯**：所有节点与关系带 provenance（§5）。
3. **语义严格**：每个关系有定义域/值域/基数；入库前经 Pydantic 校验。
4. **枚举属性优先**：性/味/归经/毒性做枚举属性，不做关系（工程验证模式，避免关系爆炸）。
5. **中西医双体系**：中医病证与西医疾病/症状分别建类，`CORRESPONDS_TO` 映射（依据 ICD-11 双重编码规则与 SymMap 映射表）。
6. **版本化**：本体 semver；本版为 0.3，M0 冻结后升 1.0。
7. **安全优先**（v0.3，ADR-013）：毒性知识治理三层防线——入库过滤、风险分级、答案强制警示与剂量隐藏（详见 §7）。

---

## 2. 实体类型（12 类）

### 2.1 Herb 中药（饮片级）

| 项 | 规范 |
|---|---|
| 定义 | 入药的中药材（饮片口径），以 2020 药典收载名为 canonical name |
| 命名规范 | 药典名优先；药典未收载用《中医药学名词》；两者皆无用 SymMap 名并降置信度 |
| 别名 | `aliases: list[str]`（异名/商品名/地方名，如 北芪→黄芪） |

属性：

| 属性 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | str | ✓ | 规范名（UNIQUE） |
| aliases | list[str] | | 别名 |
| pinyin | str | | 拼音（检索辅助） |
| latin | str | | 拉丁学名（nmm-data） |
| nature | Nature 枚举 | | 药性：寒/微寒/凉/平/温/微温/热/大热 |
| flavors | list[Flavor] | | 药味：酸/苦/甘/辛/咸/涩/淡 |
| meridians | list[Meridian] | | 归经：肺/大肠/胃/脾/心/小肠/膀胱/肾/心包/三焦/胆/肝 |
| toxicity | Toxicity 枚举 | | 无毒/小毒/有毒/大毒 |
| dose_range | str | | 常用剂量区间（统一 g，古典单位按换算表折算） |
| pregnancy_level | 枚举 | | 禁用/忌用/慎用/无标记（药典"注意"项） |
| icd11_code | str | | 预留（一般无） |
| standard_ref | str | | 规范名依据（药典2020/中医药学名词/SymMap） |

### 2.2 Formula 方剂

| 项 | 规范 |
|---|---|
| 定义 | 由 ≥2 味药按一定规则组成的处方单元（含经方/成方） |
| 命名规范 | BATMAN-TCM 名 + OpenKG 174 校准；与《方剂学》教材名对齐 |

属性：name ✓ / aliases / pinyin / source_dynasty（朝代）/ source_book（原书名，冗余便于展示）/ efficacy（功用摘要）/ usage（用法）/ formula_type（经方|时方|中成药）/ dosage_form（丸|散|膏|丹|颗粒|口服液|片|胶囊）/ manufacturer（中成药厂家，可空）/ **safety_level（safe|caution，加载时由成分毒性推导，v0.3；高危方剂不获此属性——它们根本不入库，见 §7）**。

> **v0.2 修订：中成药不设独立实体，以 `formula_type` 属性建模**（ADR-011）。比独立 PatentMedicine 实体更优：①COMPOSED_OF/TREATS_* 等关系完全复用；②"含附子的方剂"类查询天然覆盖中成药；③避免藿香正气散/藿香正气水之类同源实体被拆成两类。BATMAN 复方数据天然含中成药；二阶段可用 TCM-MKG（8977 中成药）扩充。

### 2.3 DiseaseTCM 中医疾病

- 定义：以中医理论与术语表述的疾病（GB/T 15657 A 码）。
- 命名规范：GB/T 15657 标准名。
- 属性：name ✓ / code（A 码）/ icd11_code（TM1 码，如有）/ category_path（大类层级）。

### 2.4 Pattern 证候（核心新增）

- 定义：疾病发展过程中某一阶段的病理概括（辨证论治的"证"）。
- 命名规范：GB/T 15657 B 码标准名（2060 个）。
- 属性：name ✓ / code（B 码）/ icd11_code（TM1 SE70-SJ1Z）/ category_path（八纲/病因/气血津液/脏腑/经络/六经/三焦/卫气营血 8 类，排除韩医四象）。

### 2.5 SymptomTCM 中医症状

- 定义：中医口径的症状/体征表述（SymMap 收录 2285 个）。
- 属性：name ✓ / aliases / pinyin。

### 2.6 DiseaseWM 西医疾病

- 定义：现代医学疾病（ICD-11 第 1-24 章）。
- 属性：name ✓ / icd11_code ✓ / en_name。

### 2.7 SymptomWM 西医症状

- 定义：现代医学症状（SymMap 1148 个）。
- 属性：name ✓ / en_name。

### 2.8 TreatmentMethod 治法

- 定义：治疗法则（GB/T 15657 C 码，如汗法/下法/温法/清法/补法/和解法/消法/吐法）。
- 属性：name ✓ / code（C 码）/ level（大法/具体治法）。

### 2.9 Effect 功效

- 定义：中药/方剂的功效术语（如 补气固表/清热解毒）。
- 命名规范：《中医药学名词》优先；粒度对齐 SymMap 功效字段，抽取时归一到标准词表。
- 属性：name ✓ / category（功效大类：补虚/解表/清热/活血/…约 20 类，参照中药学教材功效分类）。

### 2.10 Component 化学成分（二阶段）

- 属性：name ✓ / pubchem_cid / molecular_formula / mw。
- 第一版不加载成分数据，schema 预留。

### 2.11 Source 出处

- 定义：知识来源（典籍/标准/数据库/人工整理）。
- 属性：name ✓ / type 枚举（典籍/国家标准/药典/数据库/人工整理）/ citation（引用义务文本）/ license。

### 2.12 Category 分类

- 定义：树形分类导航节点（挂 GB/T 15657 层级、功效大类等）。
- 属性：name ✓ / tree_path ✓（如 `B02.07.03`）/ level。
- 约束：类目词不承载业务知识，仅导航。

---

## 3. 关系类型（20 种）

| 关系 | 定义域 | 值域 | 基数 | 边属性 | 语义 |
|---|---|---|---|---|---|
| COMPOSED_OF | Formula | Herb | 1:N | dose, unit, role(君/臣/佐/使), confidence | 方剂组成（剂量与角色） |
| HAS_EFFECT | Herb / Formula | Effect | N:M | source_text, confidence | 具有功效 |
| TREATS_PATTERN | Formula / Herb | Pattern | N:M | confidence | 主治证候（辨证论治核心链路） |
| TREATS_DISEASE | Formula / Herb | DiseaseTCM | N:M | confidence | 主治中医疾病 |
| INDICATES_METHOD | Formula | TreatmentMethod | N:M | — | 体现治法 |
| HAS_SYMPTOM | Pattern / DiseaseTCM | SymptomTCM | N:M | is_primary(bool), confidence | 证/病表现为症状；is_primary 依据 GB/T 16751.2 主症/次症诊断标准（辨证加权用，ADR-012） |
| ALLAYS_SYMPTOM | Formula / Herb | SymptomTCM | N:M | confidence | 缓解症状 |
| CORRESPONDS_TO | DiseaseTCM | DiseaseWM | N:M | mapping_source | 中西医疾病映射（ICD-11 双编码） |
| CORRESPONDS_TO | SymptomTCM | SymptomWM | N:M | mapping_source | 中西医症状映射（SymMap） |
| CONTRAINDICATED_WITH | Herb | Herb | N:M | type(十八反/十九畏), verse(歌诀原文) | 配伍禁忌（安全红线） |
| PREGNANCY_CONTRA | Herb | —（用节点属性+该关系挂 Category"妊娠禁忌"） | — | level | 妊娠禁/忌/慎（双轨：属性 + 可查询边） |
| SYNERGISTIC_WITH | Herb | Herb | N:M | type(相须/相使) | 七情增效 |
| RESTRAINS | Herb | Herb | N:M | type(相畏/相杀) | 七情制约 |
| PROCESSED_FROM | Herb | Herb | N:1 | method(炮制法) | 炮制品溯源（熟地黄→地黄） |
| BELONGS_TO | Herb / Formula / Pattern / DiseaseTCM / Effect | Category | N:1 | — | 分类挂载 |
| FROM_SOURCE | 除 Category 外全部 | Source | N:M | — | 知识出处（provenance 节点化） |
| CONTAINED_IN | Component | Herb | N:M | content_reference | 含有成分（二阶段） |
| HAS_INGREDIENT_ROLE | — | — | — | — | （弃用，并入 COMPOSED_OF.role） |
| MANIFESTS | DiseaseWM | SymptomWM | N:M | confidence | 西医疾病表现（SymMap） |
| ALIASED_IN | Herb / Formula | Source | N:M | — | 别名著录出处 |

### 3.1 关键链路（问答主路径）

```
症状 →(HAS_SYMPTOM)→ 证候 →(TREATS_PATTERN)→ 方剂 →(COMPOSED_OF)→ 中药 →(HAS_EFFECT)→ 功效
```

安全链路：`中药 →(CONTRAINDICATED_WITH)→ 中药`、`中药.pregnancy_level` —— Agent 答案合成时强制查询。

### 3.2 一致性检查表（A3.3）

- 12 类实体全部出现在 ≥1 关系的定义域或值域 ✓
- 无语义重复关系（HAS_INGREDIENT_ROLE 已并入 COMPOSED_OF）✓
- 禁止的边：Category 之间不连业务关系；Source 只作为值域（FROM_SOURCE/ALIASED_IN）✓

---

## 4. 枚举规范（A4.2）

```
Nature      = 寒 | 微寒 | 凉 | 平 | 温 | 微温 | 热 | 大热
Flavor      = 酸 | 苦 | 甘 | 辛 | 咸 | 涩 | 淡
Meridian    = 肺 | 大肠 | 胃 | 脾 | 心 | 小肠 | 膀胱 | 肾 | 心包 | 三焦 | 胆 | 肝
Toxicity    = 无毒 | 小毒 | 有毒 | 大毒
SafetyLevel = safe | caution            # v0.3：方剂安全分级；high_risk 不存在——被数据层过滤（ADR-013）
PregnancyLevel = 禁用 | 忌用 | 慎用 | 无标记
FormulaRole = 君 | 臣 | 佐 | 使
FormulaType = 经方 | 时方 | 中成药          # v0.2：中成药以属性建模（ADR-011）
DosageForm  = 丸 | 散 | 膏 | 丹 | 颗粒 | 口服液 | 片 | 胶囊
DoseUnit    = g | mg | ml        # 古典单位（钱/两/分）按换算表折算为 g 后入库，原始值存 raw_dose
SynonymType = 相须 | 相使 | 相畏 | 相杀   # 七情（相反→CONTRAINDICATED_WITH，恶→暂不建）
ContraType  = 十八反 | 十九畏
PatternCategory = 八纲证 | 病因证 | 气血津液证 | 脏腑证 | 经络证 | 六经证 | 三焦证 | 卫气营血证
```

古典剂量换算表（C2 交付物之一）：参照 2020 药典附录与教材通行的 1 钱≈3g、1 两≈30g（东汉 1 两≈13.8g 的考证值在 `raw_dose` 保留原文，换算值按通行标准）。

---

## 5. Provenance 规范（A4.3）

每个节点与关系统一携带：

| 字段 | 类型 | 说明 |
|---|---|---|
| source_id | str | 指向 Source 节点（FROM_SOURCE 关系同步建立） |
| confidence | float 0-1 | 规则/人工整理=1.0；数据库导入=0.95；LLM 抽取=模型自评（默认 0.7，校验通过 +0.1） |
| extracted_at | datetime | 入库时间 |
| method | 枚举 | curated（人工）/ db_import（数据库）/ llm_extract（抽取）/ manual_review（审核修正） |

冲突策略：同一事实多源冲突时，保留全部边并在边属性记录各源，展示层按 confidence 排序；`manual_review` 永远最高优先。

---

## 6. 本体版本化（A4.4）

- 版本号写入 `tcm_kg/ontology/VERSION` 与图数据库 `CALL dbms.setConfigValue` 不可用时的系统节点 `_OntologyMeta`。
- 变更流程：改本文档 → bump 版本 → 迁移脚本 `migrations/ontology_v{N}.py`（节点/关系/属性的增删改）→ 质检回归。
- 兼容性：minor 版本只增不删；major 版本允许删除但必须提供迁移脚本。

---

## 7. 安全治理规范（ADR-013，v0.3 新增）

> 用户决策：**安全优先，有毒伤害性大的方剂不入库**。本节是全项目的安全红线，优先级高于数据完整性。

### 7.1 三层防线

| 层 | 机制 | 落点 |
|---|---|---|
| **L1 数据层过滤** | 含排除清单药材的方剂**不入库**（不建 Formula 节点、不建其 COMPOSED_OF 等关系）；过滤结果记录到 `data/aligned/exclusion_report.csv`（方剂名+命中药材+决策依据），保证可审计可回溯 | pipeline C9 加载器 |
| **L2 风险分级** | 入库方剂由成分毒性推导 `safety_level`：含"有毒"级药材 → caution；仅小毒/无毒 → safe。caution 方剂保留（如四逆汤类临床常用），但详情页与答案必须带警示 | pipeline C9 推导 |
| **L3 答案层强制警示** | Agent 合成答案时强制检查：①涉及 safety_level=caution 方剂或 toxicity ≥ 有毒 药材 → 注入标准警示文案（含明确标注与医师开具引导）并**隐藏全部剂量/用法数字**（§7.4）；②妊娠相关 → 检查 pregnancy_level；③十八反十九畏配对 → 直接阻断推荐；④工具层输出消毒器先行剥离剂量字段（纵深防御第一点） | agent E7 + 工具层 E1 |

### 7.2 排除清单（L1 依据）

以国务院《医疗用毒性药品管理办法》（1988）毒性中药品种 + 药典"大毒"标记为准，在 C2.5 人工结构化为 `data/curated/toxic_exclusion.csv`，包括但不限于：**生川乌、生草乌、生附子、生半夏、生南星、生巴豆、生马钱子、生白附子、生甘遂、生狼毒、生千金子、生天仙子、斑蝥、蟾酥、砒石/砒霜、水银、雄黄（入丸散）、轻粉、红粉、洋金花、闹羊花** 等。

**炮制品豁免规则**：清单针对**生品**（毒烈品）。规范炮制品（如制川乌、法半夏、制附子）不触发 L1 过滤，但成分毒性仍参与 L2 分级——这正是 D1"炮制品独立节点"决策的价值所在：生/熟必须可区分。

### 7.3 药材节点保留策略

排除清单药材**仍建 Herb 节点**（毒性知识本身是安全教育内容：用户查"生川乌有什么危险"必须能答），但：
- 属性带 toxicity + `is_excluded_in_formula: true` 标记
- 答案层触发 L3 警示模板，不提供剂量与用法建议（剂量隐藏规则见 §7.4，毒性 ≥ 有毒 的非排除药材同样适用）

### 7.4 剂量信息呈现规则（用户决策，v0.4）

> 用户决策：**危险方剂/有毒方剂不给剂量，给出明确标注，引导去医师处开具。** 剂量数据保留在数据库（知识完整性与审计），但任何对外出口不得泄露。

**剂量隐藏矩阵**：

| 对象 | 判定条件 | 剂量处理 |
|---|---|---|
| 方剂 | safety_level = caution | COMPOSED_OF.dose、Formula.usage 在工具层/API 层一律掩码 |
| 药材 | toxicity ∈ {有毒, 大毒} | Herb.dose_range 在工具层/API 层一律掩码 |
| 药材 | is_excluded_in_formula = true | 同上，且不给使用建议（§7.3） |
| 方剂/药材 | 其余（safe / 小毒及以下） | 正常展示 |

**标准警示文案**（答案模板 + 前端警示卡片共用，E7.3/G4 实现）：

> ⚠ 该{方剂/药材}含毒性成分（{命中毒药材列表}）。出于安全考虑，此处不提供剂量与用法信息。请前往正规医疗机构，由执业中医师辨证后开具处方使用。

**实现要点（双点掩码，纵深防御）**：

1. **工具层输出消毒器**（E1.7）：所有 Agent 工具（含 text2cypher——防止生成的 Cypher 直接 RETURN dose 绕过）返回结果先过消毒器，剥离命中对象的 dose/dose_range/usage 字段
2. **API 序列化层掩码**（F3）：即使 Agent prompt 失效，HTTP 响应也不含命中对象的剂量字段
3. **前端**（G4）：命中对象显示红色警示徽标"含毒性 · 须医师开具"+ 标准警示文案，剂量行不渲染
4. 明确标注：safety_level=caution（方剂）与 toxicity（药材）即节点级标注来源，前端徽标与答案警示均由此驱动

### 7.5 验收指标

- 图谱内 Formula 节点 100% 无排除清单生品成分（D2 质检项）
- 涉毒答案警示覆盖率 100%（H1 评测项，一票否决）
- 剂量隐藏覆盖率 100%：抽样 caution 方剂/有毒药材的 API 响应与 Agent 答案，剂量/用法字段 0 泄露（H1 评测项，一票否决）
- 排除报告条目数与源数据对照可解释（审计项）

---

## 8. 待定决策（M0 裁决）

| # | 决策 | 建议 | 备选 |
|---|---|---|---|
| D1 | 炮制品建模 | 独立 Herb 节点 + PROCESSED_FROM（熟地黄≠地黄，性味功效确有差异） | 做属性（节点少但语义损失） |
| D2 | 剂量换算 | 通行标准（1钱=3g/1两=30g），原文存 raw_dose | 东汉考证值（经方研究场景） |
| D3 | DiseaseWM 粒度 | SymMap 映射 + ICD-11 码，不自行维护西医知识 | 只存映射不建西医节点（会损失 SymMap 症状映射能力） |

---

## 9. 规模预估（对照 M6 目标）

> 注：v0.3 起规模预估为 **L1 过滤后** 的净入库量；排除量以 C9 报告为准（预计影响 < 5% 方剂，主要为含生品毒药的古方）。

| 实体 | 预估来源与数量 |
|---|---|
| Herb | nmm 616（药典锚点）∪ SymMap 698 ≈ 700-800 |
| Formula | BATMAN 54832（首版抽样加载 ≥5000，天然含中成药）+ OpenKG 174；二阶段 TCM-MKG 8977 中成药扩充 |
| Pattern | GB/T 15657 2060（叶节点） |
| DiseaseTCM | GB/T 15657 1369（叶节点） |
| SymptomTCM/WM | SymMap 2285/1148 |
| DiseaseWM | SymMap 14086（仅映射所需子集） |
| Effect | 功效词表归一后约 300-500 |
| Source/Category | ~50 / ~350 |

关系量级：COMPOSED_OF ≈ 5万+（BATMAN）、禁忌 ≈ 100+、其余 N:M 合计 ≈ 数万 → 总关系 ≥ 5 万达标。

---

## 9. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-08-21 | 初稿（12 实体 / 20 关系） |
| v0.2 | 2026-08-21 | ①中成药以 Formula.formula_type 属性建模（ADR-011，替代独立实体方案）②HAS_SYMPTOM 新增 is_primary 边属性支撑辨证加权（ADR-012，数据源 GB/T 16751.2 主症/次症）③新增 FormulaType / DosageForm 枚举 ④规模预估更新 |
| v0.3 | 2026-08-21 | 新增 §7 安全治理规范（ADR-013，用户决策"安全优先"）：L1 入库过滤（毒性管理品种生品清单）→ L2 Formula.safety_level 分级 → L3 答案强制警示；新增 SafetyLevel 枚举与 is_excluded_in_formula 标记；排除报告与验收指标 |
| v0.4 | 2026-08-21 | 用户决策"毒/险方剂不给剂量、明确标注、医师开具"落地为 §7.4 剂量信息呈现规则：剂量隐藏矩阵（caution 方剂/有毒药材）、标准警示文案、工具层+API 层双点掩码（含 text2cypher 旁路防护）、验收指标新增剂量 0 泄露 |
