// ============================================================
// 文件用途：本草通 2.0（tcm-kg）Neo4j 数据库初始化脚本
// 核心功能：为知识图谱的 12 类实体（Herb/Formula/DiseaseTCM/Pattern/
//          SymptomTCM/DiseaseWM/SymptomWM/TreatmentMethod/Effect/Component/Source/Category）
//          创建 name 字段的 UNIQUE 唯一约束，保证图谱数据完整性与幂等入库
// 如何使用：
//   make init-db                  # Linux/macOS（GNU make）
//   .\scripts\tasks.ps1 init-db   # Windows PowerShell
//   验证：SHOW CONSTRAINTS
// ============================================================
// B3.3：12 类实体 name UNIQUE 约束（对应 ontology-design.md §2）。
// 应用方式：make init-db（Windows: .\scripts\tasks.ps1 init-db）
// 验证方式：SHOW CONSTRAINTS
//
// 注：Neo4j 社区版不支持属性存在性约束（Enterprise 功能）；
// 关系定义域/值域与必填属性由加载层 Pydantic 校验兜底（A8 模型），
// 并由 D1/D2 质检查询复核（MASTER_PLAN Phase D）。

CREATE CONSTRAINT herb_name_unique IF NOT EXISTS FOR (n:Herb) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT formula_name_unique IF NOT EXISTS FOR (n:Formula) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT disease_tcm_name_unique IF NOT EXISTS FOR (n:DiseaseTCM) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT pattern_name_unique IF NOT EXISTS FOR (n:Pattern) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT symptom_tcm_name_unique IF NOT EXISTS FOR (n:SymptomTCM) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT disease_wm_name_unique IF NOT EXISTS FOR (n:DiseaseWM) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT symptom_wm_name_unique IF NOT EXISTS FOR (n:SymptomWM) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT treatment_method_name_unique IF NOT EXISTS FOR (n:TreatmentMethod) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT effect_name_unique IF NOT EXISTS FOR (n:Effect) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT component_name_unique IF NOT EXISTS FOR (n:Component) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT source_name_unique IF NOT EXISTS FOR (n:Source) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT category_name_unique IF NOT EXISTS FOR (n:Category) REQUIRE n.name IS UNIQUE;

// 全文索引（E1.3 fulltext_search 工具；name + aliases，中文分词用 cjk）
CREATE FULLTEXT INDEX entity_name_fulltext IF NOT EXISTS
FOR (n:Herb|Formula|DiseaseTCM|Pattern|SymptomTCM|DiseaseWM|SymptomWM|TreatmentMethod|Effect|Component)
ON EACH [n.name];
