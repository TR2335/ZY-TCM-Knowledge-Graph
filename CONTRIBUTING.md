# 贡献指南 / Contributing

感谢关注本草通（中医知识图谱 + GraphRAG 问答系统）。欢迎通过 Issue 反馈问题，或提交 Pull Request。

## 开发环境

- 前置要求：Python 3.13、[uv](https://docs.astral.sh/uv/)、Docker（用于 Neo4j，可选）。
- 安装依赖并启动本地基础设施：

```bash
# 启动 Neo4j（可选，仅数据入库/问答联调时需要）
docker compose up -d

# 安装后端依赖
cd backend
uv sync

# 安装 pre-commit 钩子（ruff 检查 + 格式化）
uv run pre-commit install
```

- 后端代码位于 `backend/`（typer CLI 入口 `tcm`），前端为 `frontend/` 单文件页面。

## 提交前检查

质量门禁一条命令跑完（ruff + mypy strict + pytest 全绿）：

```bash
make check          # Windows 可用 scripts/tasks.ps1 中的同名目标
```

也可分步执行：

```bash
make lint           # ruff check + ruff format --check
make typecheck      # mypy strict
make test           # pytest
```

## Commit 规范

采用 Conventional Commits 风格，格式：`<type>(<scope>): <简短描述>`。

常用 type：

| type | 用途 |
| --- | --- |
| `feat` | 新增功能 |
| `fix` | 修复缺陷 |
| `docs` | 文档变更 |
| `refactor` | 重构（不改行为） |
| `test` | 测试相关 |
| `chore` | 工程配置、构建脚本等杂项 |

- 一个 commit 只做一件事，描述使用祈使句、中英文皆可。
- 涉及本体或安全策略的变更，建议在 commit 正文中注明依据（ADR 编号或官方标准出处）。

## 安全注意事项

- **严禁提交密钥**：不要提交 `backend/.env` 或任何含真实凭据的文件，配置模板只通过 `.env.example` 维护。
- **不要提交大数据**：`data/raw/`、`data/parsed/`、`data/extracted/`、`data/models/`、`data/cache/` 等可再生产数据、下载的数据集与本地模型权重均不入库（`.gitignore` 已覆盖；如新增路径请先更新 `.gitignore`）。`data/curated/` 为人工整理的安全清单资产，随库版本化，改动需谨慎。
- 提交前自查 `git status` 与 `git diff --staged`，确认没有混入凭据、个人路径或大文件。
- 涉及毒性药材、配伍禁忌等安全清单的修改，请在 PR 描述中注明官方来源，便于审计回溯。
