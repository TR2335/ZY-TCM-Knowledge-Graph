# ===================================================================
# 文件：cli.py
# 用途：命令行接口，提供 tcm download/parse/extract/align/load/reindex/chat/serve
#       等子命令，是数据管线和服务的统一入口
# 所属层级：CLI 入口层（B7 骨架 · 串联 Phase C 数据管线与 Phase E/F 服务）
# 核心功能：
#   - download 下载冷启动数据源、parse 解析归一为统一 JSONL、extract LLM 结构化抽取
#   - align 实体对齐（召回+精判+审核队列）、load 入库加载（含 L1/L2 安全过滤）
#   - reindex 重建向量索引、chat 命令行问答、serve 启动 FastAPI 服务
# 如何使用：
#   安装后执行 `tcm --help` 查看子命令；如 `tcm serve --port 8000`、`tcm load`、
#   `tcm chat`；pyproject [project.scripts] tcm 指向 main()。
# ===================================================================

"""tcm CLI（B7 骨架）：数据管线与服务入口。

子命令在 Phase C（数据管线）/ Phase E（Agent）逐步接入实现；
当前为带帮助文档的占位实现，`tcm --help` 可查。
"""

from pathlib import Path

import typer

from tcm_kg.core.logging import setup_logging
from tcm_kg.pipeline.download import download_all
from tcm_kg.pipeline.parse import PARSERS, format_report, parse_cli
from tcm_kg.pipeline.sources import ALL_SOURCES

# Typer 应用主对象：注册所有 tcm 子命令，无参数时打印帮助
app = typer.Typer(
    name="tcm",
    help="本草通 2.0（tcm-kgs）：中医知识图谱数据管线与服务 CLI",
    no_args_is_help=True,
    add_completion=False,
)

# 项目根（backend/src/tcm_kg/cli.py -> tcm-kgs/）
_REPO_ROOT = Path(__file__).resolve().parents[3]


# 占位子命令实现：未接入阶段打印提示并以非零码退出
def _stub(phase: str) -> None:
    typer.secho(f"尚未实现：将于 Phase {phase} 接入。", fg=typer.colors.YELLOW)
    raise typer.Exit(code=1)


@app.command()
def download(
    raw_dir: Path | None = typer.Option(  # noqa: B008
        None, "--raw-dir", help="数据落盘目录（默认 <repo>/data/raw）。"
    ),
    legacy_src: Path | None = typer.Option(  # noqa: B008
        None,
        "--legacy-src",
        help="旧项目已爬 txt 目录（默认 <repo>/../__001__clawler；不存在则跳过）。",
    ),
    force: bool = typer.Option(False, "--force", help="忽略本地已有文件强制重下。"),  # noqa: B008
) -> None:
    """下载冷启动数据源（SymMap/BATMAN/nmm-data/OpenKG/国标 PDF）→ data/raw/（C1）。"""
    setup_logging(dev=True)
    target_raw = raw_dir or (_REPO_ROOT / "data" / "raw")
    legacy_default = _REPO_ROOT.parent / "__001__clawler"
    legacy: Path | None
    if legacy_src is not None:
        legacy = legacy_src
    elif legacy_default.is_dir():
        legacy = legacy_default
    else:
        typer.secho(f"旧项目目录不存在，跳过 legacy 复制：{legacy_default}", fg=typer.colors.YELLOW)
        legacy = None
    stats = download_all(
        ALL_SOURCES,
        target_raw,
        legacy_src=legacy,
        force=force,
    )
    typer.echo(
        f"完成：下载 {stats['downloaded']} 个文件，跳过（未变化）{stats['skipped']} 个，"
        f"legacy 复制 {stats['legacy_copied']} 个 → {target_raw}"
    )


# 解析归一：各源 → 统一中间 JSONL（data/parsed）
@app.command()
def parse(
    source: str = typer.Option(  # noqa: B008
        "all", "--source", help=f"数据源（{'/'.join(PARSERS)}/all）。"
    ),
    raw_dir: Path | None = typer.Option(  # noqa: B008
        None, "--raw-dir", help="原始数据目录（默认 <repo>/data/raw）。"
    ),
    out_dir: Path | None = typer.Option(  # noqa: B008
        None, "--out-dir", help="输出目录（默认 <repo>/data/parsed）。"
    ),
) -> None:
    """解析归一：各源 → 统一中间 JSONL（C3）。"""
    setup_logging(dev=True)
    target_raw = raw_dir or (_REPO_ROOT / "data" / "raw")
    target_out = out_dir or (_REPO_ROOT / "data" / "parsed")
    try:
        stats = parse_cli([source], target_raw, target_out)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.echo(format_report(stats))
    typer.echo(f"输出 → {target_out}")


# LLM 结构化抽取（旧项目 txt + 药典文本，带 provenance）→ data/extracted
@app.command()
def extract(
    source: str = typer.Option(  # noqa: B008
        "all", "--source", help="抽取对象（herb/formula/all，对应 legacy 中药/方剂 txt）。"
    ),
    limit: int | None = typer.Option(  # noqa: B008
        None, "--limit", help="仅处理前 N 个词条（冒烟测试用）。"
    ),
    raw_dir: Path | None = typer.Option(  # noqa: B008
        None, "--raw-dir", help="原始数据目录（默认 <repo>/data/raw）。"
    ),
    out_dir: Path | None = typer.Option(  # noqa: B008
        None, "--out-dir", help="输出目录（默认 <repo>/data/extracted）。"
    ),
) -> None:
    """LLM 结构化抽取（旧项目 txt + 药典文本，带 provenance）（C4）。"""
    import asyncio

    setup_logging(dev=True)
    from tcm_kg.core.settings import get_settings
    from tcm_kg.pipeline.extract import extract_cli, format_extract_report
    from tcm_kg.pipeline.llm import build_llm_from_settings

    kinds = ["herb", "formula"] if source == "all" else [source]
    target_raw = raw_dir or (_REPO_ROOT / "data" / "raw")
    target_out = out_dir or (_REPO_ROOT / "data" / "extracted")
    try:
        llm = build_llm_from_settings()
    except Exception as exc:  # noqa: BLE001 配置缺失提示
        typer.secho(f"LLM 配置不可用：{exc}", fg=typer.colors.RED)
        typer.secho(
            "请在 backend/.env 配置 TCM_LLM__BASE_URL / TCM_LLM__API_KEY / TCM_LLM__MODEL",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1) from exc
    stats = asyncio.run(
        extract_cli(
            target_raw,
            target_out,
            llm,
            kinds=kinds,
            limit=limit,
            concurrency=get_settings().pipeline.llm_concurrency,
        )
    )
    typer.echo(format_extract_report(stats))
    typer.echo(f"输出 → {target_out}")


# 实体对齐：召回 + 精判 + 审核队列（可选 LLM 精判，默认规则）→ data/aligned
@app.command()
def align(
    label: str = typer.Option(  # noqa: B008
        "all", "--label", help="对齐实体类型（Herb/Formula/Pattern/.../all）。"
    ),
    topk: int = typer.Option(  # noqa: B008
        10, "--topk", help="召回候选数（C6.2）。"
    ),
    use_llm: bool = typer.Option(  # noqa: B008
        False, "--llm", help="LLM 精判（C7.1）；默认规则精判（无需 LLM 配置）。"
    ),
    parsed_dir: Path | None = typer.Option(  # noqa: B008
        None, "--parsed-dir", help="中间数据目录（默认 <repo>/data/parsed）。"
    ),
    out_dir: Path | None = typer.Option(  # noqa: B008
        None, "--out-dir", help="输出目录（默认 <repo>/data/aligned）。"
    ),
) -> None:
    """实体对齐：召回 + 精判 + 审核队列（C6-C8）。"""
    setup_logging(dev=True)
    from tcm_kg.pipeline.align import align_label_pool, build_pool, write_outputs
    from tcm_kg.pipeline.embedding import build_embedder_from_settings

    target_parsed = parsed_dir or (_REPO_ROOT / "data" / "parsed")
    target_out = out_dir or (_REPO_ROOT / "data" / "aligned")
    pool = build_pool(target_parsed)
    if label != "all":
        pool = {k: v for k, v in pool.items() if k == label}
    pool = {k: v for k, v in pool.items() if v}
    if not pool:
        typer.secho(
            f"未发现待对齐实体（label={label}，parsed 目录：{target_parsed}）",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)
    typer.echo(f"候选池：{ {k: len(v) for k, v in pool.items()} }")

    embedder = build_embedder_from_settings()
    judge = None
    if use_llm:
        from tcm_kg.pipeline.align import LLMJudge
        from tcm_kg.pipeline.llm import build_llm_from_settings

        try:
            judge = LLMJudge(build_llm_from_settings())
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"LLM 精判不可用（{exc}），降级规则精判", fg=typer.colors.YELLOW)

    all_decisions, all_review, all_merge = [], [], []
    for pool_label, entries in pool.items():
        typer.echo(f"[{pool_label}] 召回+精判 {len(entries)} 条目 …")
        decisions, review, merge = align_label_pool(entries, embedder, topk=topk, judge=judge)
        same_n = sum(1 for d in decisions if d.verdict == "same")
        related_n = sum(1 for d in decisions if d.verdict == "related")
        different_n = sum(1 for d in decisions if d.verdict == "different")
        typer.echo(
            f"[{pool_label}] 判定 {len(decisions)} 对：same {same_n} / related {related_n}"
            f" / different {different_n}；待审核 {len(review)}，自动合并候选 {len(merge)}"
        )
        all_decisions.extend(decisions)
        all_review.extend(review)
        all_merge.extend(merge)
    write_outputs(target_out, all_decisions, all_review, all_merge)
    typer.echo(
        f"合计：判定 {len(all_decisions)} 对｜待审核 {len(all_review)}"
        f"｜合并候选 {len(all_merge)} → {target_out}"
    )


# 入库加载（100 条垂直切片，含 L1 安全过滤与 L2 分级推导）→ Neo4j
@app.command()
def load(
    parsed_dir: Path | None = typer.Option(  # noqa: B008
        None, "--parsed-dir", help="中间数据目录（默认 <repo>/data/parsed）。"
    ),
    report: Path | None = typer.Option(  # noqa: B008
        None, "--report", help="L1 排除报告输出路径（默认 <repo>/data/exclusion_report.csv）。"
    ),
) -> None:
    """入库加载（100 条垂直切片，含 L1 安全过滤与 L2 分级推导）（C9）。"""
    setup_logging(dev=True)
    from neo4j.exceptions import ServiceUnavailable

    from tcm_kg.graphdb.repo import build_repo_from_settings
    from tcm_kg.pipeline.load import load_100

    target_parsed = parsed_dir or (_REPO_ROOT / "data" / "parsed")
    target_report = report or (_REPO_ROOT / "data" / "exclusion_report.csv")
    try:
        repo = build_repo_from_settings()
    except ServiceUnavailable as exc:
        typer.secho(f"Neo4j 不可达：{exc}", fg=typer.colors.RED)
        typer.secho("请先启动：make up（或手动 docker compose up -d）", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc
    constraints = (_REPO_ROOT / "scripts" / "init_constraints.cypher").read_text(encoding="utf-8")
    applied = repo.apply_constraints(constraints)
    typer.echo(f"约束/索引语句已应用：{applied}")
    with repo:
        result = load_100(target_parsed, repo, target_report)
    typer.echo(f"切片规模：{result['slice']}")
    typer.echo(f"L1 排除方剂：{result['l1_excluded']}｜L2 caution：{result['l2_caution']}")
    typer.echo(f"图谱节点：{result['graph_labels']}")
    typer.echo(f"图谱关系：{result['graph_rels']}")
    typer.echo(f"排除报告 → {target_report}")


# 重建向量索引：bge-m3 嵌入 + HNSW（占位，待接入）
@app.command()
def reindex() -> None:
    """重建向量索引：bge-m3 嵌入 + HNSW（C10）。"""
    _stub("C")


# 命令行问答：交互式调用 Agent（LLM 未配置时降级规则 Agent）
@app.command()
def chat() -> None:
    """命令行问答（Agent 交互；LLM 未配置时规则 Agent）。"""
    import asyncio

    setup_logging(dev=True)
    from tcm_kg.agent.graph import RuleBasedAgent, ToolLoopAgent
    from tcm_kg.agent.tools import AgentTools
    from tcm_kg.graphdb.repo import build_repo_from_settings
    from tcm_kg.pipeline.llm import build_llm_from_settings

    try:
        repo = build_repo_from_settings()
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Neo4j 不可达：{exc}", fg=typer.colors.RED)
        typer.secho("请先启动：make up && tcm load", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc
    tools = AgentTools(repo)
    agent: ToolLoopAgent | RuleBasedAgent
    try:
        agent = ToolLoopAgent(build_llm_from_settings(), tools)
        typer.echo("（LLM Agent 已启用）")
    except Exception:  # noqa: BLE001
        agent = RuleBasedAgent(tools)
        typer.echo("（LLM 未配置，规则问答模式；配置 backend/.env 可启用 LLM）")
    typer.echo("输入问题（quit 退出）。示例：查询「附子」详情 / 心悸失眠是什么证")
    while True:
        try:
            question = typer.prompt("\n问题").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in ("quit", "exit", "q"):
            break
        answer = asyncio.run(agent.answer(question))
        typer.echo(f"\n{answer}\n")


# 启动 FastAPI 服务（uvicorn 托管 create_default_app）
@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址。"),  # noqa: B008
    port: int = typer.Option(8000, "--port", help="监听端口。"),  # noqa: B008
) -> None:
    """启动 FastAPI 服务（Phase F）。"""
    setup_logging(dev=True)
    import uvicorn

    from tcm_kg.api.app import create_default_app

    typer.echo(f"API → http://{host}:{port}/docs")
    uvicorn.run(create_default_app(), host=host, port=port)


# CLI 入口：pyproject [project.scripts] tcm 指向此函数，转发给 Typer app
def main() -> None:
    """入口（pyproject [project.scripts] tcm 指向此函数）。"""
    app()


if __name__ == "__main__":
    main()
