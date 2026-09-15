# ============================================================
# 文件用途：本草通 2.0（tcm-kg）项目任务脚本（GNU make；Windows 用 scripts/tasks.ps1 同名目标）
# 核心功能：统一封装 Docker 基础设施与数据管线操作，提供常用快捷命令
# 常用命令：
#   make up                 # 启动 Neo4j 容器（日常开发）
#   make up-observability   # 启动 Neo4j + Langfuse 可观测栈（profile 隔离）
#   make init-db            # 应用 12 类实体 UNIQUE 约束（scripts/init_constraints.cypher）
#   make sync && tcm load   # 安装依赖并执行数据入库（tcm CLI 子命令）
#   make test               # 运行 pytest 测试套件
#   tcm serve               # 启动 FastAPI 服务 + 静态托管前端（tcm CLI 子命令）
#   make check              # 质量门禁：ruff + mypy strict + pytest 全绿（B4 验收 / M1 门禁）
# ============================================================
# 本草通 2.0（tcm-kg）常用任务（GNU make；Windows 用 scripts/tasks.ps1 同名目标）
# 质量门禁：make check（ruff + mypy strict + pytest 全绿，B4 验收 / M1 门禁）

.PHONY: up up-observability down logs neo4j-shell init-db check lint fmt typecheck test sync schema-export

up:
	docker compose up -d

up-observability:
	docker compose --profile observability up -d

down:
	docker compose down

logs:
	docker compose logs -f neo4j

neo4j-shell:
	docker exec -it tcm-kgs-neo4j cypher-shell -u neo4j -p $${NEO4J_PASSWORD:-tcmkg-dev-password}

init-db:
	docker exec -i tcm-kgs-neo4j cypher-shell -u neo4j -p $${NEO4J_PASSWORD:-tcmkg-dev-password} < scripts/init_constraints.cypher

check: lint typecheck test

lint:
	cd backend && uv run ruff check src tests scripts
	cd backend && uv run ruff format --check src tests scripts

fmt:
	cd backend && uv run ruff format src tests scripts
	cd backend && uv run ruff check --fix src tests scripts

typecheck:
	cd backend && uv run mypy src tests scripts

test:
	cd backend && uv run pytest

sync:
	cd backend && uv sync

schema-export:
	cd backend && uv run python scripts/export_ontology_schema.py
