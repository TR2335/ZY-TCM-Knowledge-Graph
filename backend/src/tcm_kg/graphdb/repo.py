# ===================================================================
# 文件：repo.py
# 用途：Neo4j 图数据库仓储层，封装节点/关系的 CRUD 操作
# 所属层级：graphdb 层（C9 · 数据访问层，被 agent 工具层与 pipeline 加载层依赖）
# 核心功能：
#   - driver 管理与连接校验、上下文（with）自动关闭
#   - merge_entity / merge_relation：参数化 Cypher MERGE 幂等写入（防注入）
#   - run：通用只读查询；counts_by_label/counts_by_rel：节点/关系计数
#   - apply_constraints：执行 init_constraints.cypher 建约束/索引
#   - load_envelopes：批量加载 envelope 流（实体先行、关系后行，属性展平 prov_*）
# 如何使用：
#   build_repo_from_settings() 从配置构造 Neo4jRepo；或直接 Neo4jRepo(uri,user,password)
#   作为 AgentTools 的 repo 注入；CLI `tcm load` 调 load_envelopes 入库。
# ===================================================================

"""Neo4j 仓储层（C9）：driver 管理 + Cypher MERGE 加载。

设计（MASTER_PLAN C9 + ADR-003/013）：
- 12 类实体 name UNIQUE 约束由 scripts/init_constraints.cypher 建立；
- 实体 MERGE ON CREATE/ON MATCH SET 属性（幂等，重跑节点数不变）；
- 边 MERGE (start)-[r:TYPE]->(end) 按端点去重；
- provenance 展平为 prov_* 前缀属性（图谱内可查询溯源）。
"""

from __future__ import annotations  # 延迟注解求值，支持前向类型引用

from collections.abc import Iterable  # Iterable 抽象基类（类型注解用）
from typing import Any  # 任意类型（属性字典等弱类型场景）

from neo4j import Driver as Neo4jDriver  # Neo4j 驱动类型（仅类型注解用）
from neo4j import GraphDatabase  # Neo4j 官方驱动入口工厂

from tcm_kg.core.logging import get_logger  # 结构化日志器获取函数

logger = get_logger(__name__)  # 本模块结构化日志器

# 实体 MERGE Cypher 模板：按 name 幂等 upsert，ON CREATE 记录 created_at
# MERGE：按 (label, name) 复合定位节点——存在即匹配、缺失即创建（重跑节点数不变）
# ON CREATE SET：首次创建时写属性 + 记录创建时间；ON MATCH SET：已存在时覆盖属性
_ENTITY_CYPHER = """
MERGE (n:{label} {{name: $name}})
ON CREATE SET n += $props, n.created_at = datetime()
ON MATCH  SET n += $props
"""

# 关系 MERGE Cypher 模板：两端实体存在才建边，ON CREATE 写入边属性
# MATCH a/b：分别按 (标签, name) 匹配起止端点，端点缺失则本条不建边
# MERGE (a)-[r:TYPE]->(b)：按端点 + 关系类型去重建边，重复执行不产生重复边
_RELATION_CYPHER = """
MATCH (a:{start_label} {{name: $start_name}})
MATCH (b:{end_label} {{name: $end_name}})
MERGE (a)-[r:{rel_type}]->(b)
ON CREATE SET r += $props
"""


# Neo4j 写入/查询仓储：参数化 Cypher 防注入，driver 单例 + 连接校验
class Neo4jRepo:
    """Neo4j 写入/查询仓储（参数化 Cypher，防注入）。"""

    # 初始化 driver：禁用 UNRECOGNIZED 通知避免刷屏，并校验连通性
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        # 过滤 UNRECOGNIZED 通知（未用到的属性/关系类型提示），避免刷屏
        self._driver: Neo4jDriver = GraphDatabase.driver(  # 创建驱动实例（惰性建连）
            uri,  # 连接地址（bolt:// 或 neo4j:// 协议）
            auth=(user, password),  # 基本认证：用户名 + 密码
            notifications_disabled_classifications=["UNRECOGNIZED"],  # 屏蔽提示类通知
        )
        self._database = database  # 目标数据库名（默认 neo4j 库）
        self._driver.verify_connectivity()  # 启动即校验连通，失败快速报错

    def close(self) -> None:  # 显式关闭入口（也可由 with 自动触发）
        self._driver.close()  # 关闭驱动并释放底层连接池

    def ping(self) -> bool:
        """轻量依赖探针；不读取业务数据。"""
        try:  # 探活容错：失败不抛异常，只返回 False
            self._driver.verify_connectivity()  # 复用连通性校验作为探活手段
            return True  # 校验通过
        except Exception as exc:  # noqa: BLE001
            logger.warning("neo4j_not_ready", error=str(exc)[:200])  # 记录警告（截断防刷屏）
            return False  # 探活失败

    # 上下文管理：进入时返回 self，便于 with Neo4jRepo(...) as repo: 用法
    def __enter__(self) -> Neo4jRepo:
        return self  # with 进入时返回自身作为仓储对象

    def __exit__(self, *_exc: object) -> None:  # 忽略异常三元组参数
        self.close()  # 退出 with 时自动关闭驱动

    # ---------- 单条写入 ----------

    def merge_entity(self, label: str, name: str, props: dict[str, Any]) -> None:
        """MERGE 实体（幂等）。"""
        with self._driver.session(database=self._database) as session:  # 打开目标库会话
            session.execute_write(  # 显式写事务（保证 MERGE 落库）
                lambda tx: tx.run(  # 事务函数内执行 Cypher
                    # label 拼入模板，name/props 走 $ 参数防注入
                    _ENTITY_CYPHER.format(label=label), name=name, props=props
                ).consume()
            )

    def merge_relation(
        self,
        start_label: str,
        start_name: str,
        end_label: str,
        end_name: str,
        rel_type: str,
        props: dict[str, Any],
    ) -> bool:
        """MERGE 边（两端存在才建）。"""
        with self._driver.session(database=self._database) as session:  # 打开目标库会话
            result = session.execute_write(  # 写事务执行并返回结果摘要
                lambda tx: tx.run(  # 事务函数内执行关系 MERGE
                    _RELATION_CYPHER.format(  # 标签/类型拼入模板（调用方保证合法）
                        start_label=start_label, end_label=end_label, rel_type=rel_type
                    ),
                    start_name=start_name,  # 起点实体名参数
                    end_name=end_name,  # 终点实体名参数
                    props=props,  # 边属性参数
                ).consume()
            )
            return result.counters.relationships_created >= 0  # 执行成功即 True（≥0 恒真）

    # ---------- 查询 ----------

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:  # 通用只读查询入口
        with self._driver.session(database=self._database) as session:  # 打开目标库会话
            return [dict(record) for record in session.run(query, **params)]  # 记录转 dict

    # 按节点标签统计数量（图谱规模自检）
    def counts_by_label(self) -> dict[str, int]:
        # Cypher：扫描全部节点，取第一个标签分组计数（图谱规模自检）
        records = self.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt")
        return {r["label"]: r["cnt"] for r in records}  # 组装 {标签: 数量}

    # 按关系类型统计数量（图谱规模自检）
    def counts_by_rel(self) -> dict[str, int]:
        # Cypher：扫描全部有向关系，按 type(r) 分组计数
        records = self.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS cnt")
        return {r["t"]: r["cnt"] for r in records}  # 组装 {关系类型: 数量}

    # 执行 init_constraints.cypher：按分号拆分逐语句执行，忽略注释行，返回应用条数
    def apply_constraints(self, cypher_script: str) -> int:
        """执行 init_constraints.cypher（逐语句，忽略注释行）。"""
        statements = [s.strip() for s in cypher_script.split(";") if s.strip()]  # 分号拆分语句
        applied = 0  # 已成功执行的语句计数
        for stmt in statements:  # 逐条执行（约束/索引属 DDL）
            # 去掉语句内 // 行注释（init_constraints.cypher 自带说明注释）
            clean = "\n".join(
                line for line in stmt.splitlines() if not line.strip().startswith("//")
            ).strip()
            if clean:  # 拆分产生的空语句直接跳过
                with self._driver.session(database=self._database) as session:  # 每条独立会话
                    session.run(clean).consume()  # 执行建约束/索引语句
                applied += 1  # 计数累计
        return applied  # 返回应用条数

    # ---------- 批量加载 ----------

    # 批量加载 envelope 流：实体先行、关系后行（两遍保证端点就绪），分批写入
    def load_envelopes(
        self, envelopes: Iterable[dict[str, Any]], batch_size: int = 500
    ) -> dict[str, int]:
        """批量加载 envelope 流（entity 先行、relation 后行——两遍保证端点就绪）。"""
        envelopes = list(envelopes)  # 物化迭代器（两遍遍历需要）
        entities = [e for e in envelopes if e["kind"] == "entity"]  # 筛出实体信封
        relations = [e for e in envelopes if e["kind"] == "relation"]  # 筛出关系信封
        stats = {"entities": 0, "relations": 0}  # 加载统计（实体/关系各计数）

        for start in range(0, len(entities), batch_size):  # 实体先行，按批次切分
            batch = entities[start : start + batch_size]  # 当前批切片
            with self._driver.session(database=self._database) as session:  # 批级会话
                session.execute_write(self._write_entity_batch, batch)  # 写事务批量 MERGE
            stats["entities"] += len(batch)  # 累计已写入实体数

        for start in range(0, len(relations), batch_size):  # 关系后行（端点已就绪）
            batch = relations[start : start + batch_size]  # 当前批切片
            with self._driver.session(database=self._database) as session:  # 批级会话
                session.execute_write(self._write_relation_batch, batch)  # 写事务批量建边
            stats["relations"] += len(batch)  # 累计已写入关系数

        logger.info("load_done", **stats)  # 结构化记录加载完成统计
        return stats  # 返回统计字典

    # 展平 envelope record 为节点/关系属性：剔除 name/start/end，provenance 展平为 prov_* 前缀
    @staticmethod
    def _flatten_props(record: dict[str, Any]) -> dict[str, Any]:
        """envelope record → 节点/关系属性（剔除 name/start/end/provenance，展平 prov_*）。"""
        record = dict(record)  # 浅拷贝，避免修改原信封记录
        provenance = record.pop("provenance", {})  # 摘出溯源字段（默认空字典）
        record.pop("start", None)  # 移除关系起点（MERGE 键单独传）
        record.pop("end", None)  # 移除关系终点（同上）
        record.pop("name", None)  # 移除实体名（作 MERGE 键单独传）
        # 剩余业务属性 + prov_ 前缀展平的溯源字段一并返回
        return {
            **record,  # 剩余业务属性原样保留
            "prov_source_id": provenance.get("source_id"),  # 溯源源 ID
            "prov_method": provenance.get("method"),  # 抽取方法
            "prov_confidence": provenance.get("confidence"),  # 置信度
        }

    # 事务回调：批量 MERGE 实体（展平属性后按 name 写入）
    @classmethod
    def _write_entity_batch(cls, tx: Any, batch: list[dict[str, Any]]) -> None:
        for envelope in batch:  # 批内逐条 MERGE（复用同一事务）
            props = cls._flatten_props(envelope["record"])  # 展平为节点属性
            tx.run(  # 事务内执行参数化 Cypher
                _ENTITY_CYPHER.format(label=envelope["label"]),  # 标签拼入模板
                name=envelope["record"]["name"],  # MERGE 匹配键
                props=props,  # 其余节点属性
            ).consume()  # 消费结果游标

    # 事务回调：批量 MERGE 关系（按 start/end 端点 + 类型建边）
    @classmethod
    def _write_relation_batch(cls, tx: Any, batch: list[dict[str, Any]]) -> None:
        for envelope in batch:  # 批内逐条建边（复用同一事务）
            record = envelope["record"]  # 取关系信封的 record 载荷
            start, end = record["start"], record["end"]  # 解包起止端点（标签+名称）
            props = cls._flatten_props(record)  # 展平为边属性（含 prov_*）
            tx.run(  # 事务内执行参数化 Cypher
                _RELATION_CYPHER.format(  # 端点标签/关系类型拼入模板
                    start_label=start["label"],  # 起点标签
                    end_label=end["label"],  # 终点标签
                    rel_type=envelope["type"],  # 关系类型
                ),
                start_name=start["name"],  # 起点匹配键
                end_name=end["name"],  # 终点匹配键
                props=props,  # 边属性参数
            ).consume()  # 消费结果游标


# 从全局配置构造 Neo4jRepo（连接失败抛异常由 CLI 提示）
def build_repo_from_settings() -> Neo4jRepo:
    """从配置构造（连接失败抛异常由 CLI 提示）。"""
    from tcm_kg.core.settings import load_neo4j_settings  # 函数内导入避免循环依赖

    cfg = load_neo4j_settings()  # 读取 Neo4j 连接配置
    return Neo4jRepo(  # 用配置构造仓储实例
        uri=cfg.uri,  # 连接地址
        user=cfg.user,  # 用户名
        password=cfg.password.get_secret_value(),  # SecretStr 解包出明文密码
        database=cfg.database,  # 目标数据库名
    )
