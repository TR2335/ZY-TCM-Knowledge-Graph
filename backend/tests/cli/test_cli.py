# ===================================================================
# 文件：backend/tests/cli/test_cli.py（cli 层单测 · 命令行骨架 B7）
# 用途：CLI 验收三件事——① --help 列出全部 8 个子命令
#       ② 每个子命令自己的 --help 可用且非空
#       ③ reindex 仍是占位 stub（调用即报"尚未实现"，其余子命令均已实现）
# 关键设计：CliRunner 内存驱动，不真跑下载/加载等重命令
# ===================================================================

"""CLI 骨架单测（B7 验收：--help 可用）。"""

from typer.testing import CliRunner  # Typer 官方测试运行器：内存中调用 CLI 并捕获输出

from tcm_kg.cli import app  # 被测对象：Typer 应用（全部子命令注册于此）

runner = CliRunner()  # 全局共享一个运行器（无状态，可复用）

# 全部 8 个子命令清单：数据管道 6 步 + 交互 2 入口
_ALL_COMMANDS = ["download", "parse", "extract", "align", "load", "reindex", "chat", "serve"]


def test_help_lists_all_subcommands() -> None:
    """根 --help：8 个子命令一个不少（防止误删命令没被发现）。"""
    result = runner.invoke(app, ["--help"])  # 执行 tcm --help
    assert result.exit_code == 0  # 正常退出
    for command in _ALL_COMMANDS:  # 逐个检查
        assert command in result.output  # 帮助文本必须提到每个子命令名


def test_each_subcommand_has_help() -> None:
    """每个子命令的 --help：退出码 0 且输出非空（帮助文档不缺位）。"""
    for command in _ALL_COMMANDS:  # 逐个子命令
        result = runner.invoke(app, [command, "--help"])  # 执行 tcm <cmd> --help
        assert result.exit_code == 0, f"{command} --help 失败"  # 失败时报出具体是哪个命令
        assert result.output.strip()  # 帮助文本非空


def test_stub_command_signals_not_implemented() -> None:
    """除 reindex 外全部已实现；reindex 仍是占位 stub。"""
    result = runner.invoke(app, ["reindex"])  # 执行 tcm reindex（向量重建索引）
    assert result.exit_code == 1  # 非零退出码：明确告知"不可用"
    assert "尚未实现" in result.output  # 提示文案必须含"尚未实现"（而不是静默失败或报错栈）
