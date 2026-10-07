from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from .config import load_config
from .server import run_server
from .service import DbtAgentsService

app = typer.Typer(no_args_is_help=True, help="Policy-enforced dbt and warehouse tools.")


def _service(config: Path) -> DbtAgentsService:
    return DbtAgentsService(load_config(config))


def _print(result: dict[str, Any]) -> None:
    typer.echo(json.dumps(result, indent=2, sort_keys=True, default=str))
    if result.get("ok") is False:
        raise typer.Exit(1)


def _config_option() -> Path:
    return Path(os.getenv("DBT_AGENTS_CONFIG", "dbt-agents.yml"))


@app.command("check-config")
def check_config(
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Validate configuration without connecting to the warehouse."""
    value = load_config(config)
    typer.echo(json.dumps({"valid": True, "projects": sorted(value.projects)}, indent=2))


@app.command("project")
def project_get(
    project: str,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).project_get(project))


@app.command("status")
def repo_status(
    project: str,
    diff: bool = typer.Option(True, "--diff/--no-diff"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).repo_status(project, diff))


@app.command("search")
def repo_search(
    project: str,
    query: str,
    path: str = ".",
    regex: bool = False,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).repo_search(project, query, path, regex))


@app.command("read")
def repo_read(
    project: str,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).repo_read(project, path, start_line, end_line))


@app.command("dbt-inspect")
def dbt_inspect(
    project: str,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).dbt_inspect(project))


@app.command("dbt-compile")
def dbt_compile(
    project: str,
    selector: str | None = None,
    target: str | None = None,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).dbt_compile(project, selector, target))


@app.command("warehouse-describe")
def warehouse_describe(
    project: str,
    relation: str,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).warehouse_describe(project, relation))


@app.command("warehouse-query")
def warehouse_query(
    project: str,
    sql: str,
    dry_run: bool = False,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).warehouse_query(project, sql, dry_run))


@app.command("plan")
def change_plan(
    project: str,
    action: str,
    details_json: str = typer.Argument(help="Exact JSON object for the planned write"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Persist a short-lived, one-use approval plan."""
    try:
        details = json.loads(details_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"details_json is invalid: {exc}") from exc
    if not isinstance(details, dict):
        raise typer.BadParameter("details_json must be a JSON object")
    _print(_service(config).change_plan(project, action, details))


@app.command("apply")
def repo_apply_patch(
    project: str,
    path: str,
    content_file: Path = typer.Option(..., exists=True, dir_okay=False),
    plan_id: str = typer.Option(...),
    expected_sha256: str | None = typer.Option(None),
    approve: bool = typer.Option(False, "--approve"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Apply a planned, hash-guarded file replacement."""
    content = content_file.read_text(encoding="utf-8")
    _print(
        _service(config).repo_apply_patch(
            project,
            path,
            content,
            expected_sha256,
            approved=approve,
            plan_id=plan_id,
        )
    )


@app.command("dbt-test")
def dbt_test(
    project: str,
    selector: str,
    plan_id: str = typer.Option(...),
    target: str | None = None,
    approve: bool = typer.Option(False, "--approve"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Run a planned and approved dbt test selection."""
    _print(
        _service(config).dbt_test(
            project,
            selector,
            target=target,
            approved=approve,
            plan_id=plan_id,
        )
    )


@app.command("dbt-execute")
def dbt_execute(
    project: str,
    command: str,
    selector: str,
    plan_id: str = typer.Option(...),
    target: str | None = None,
    full_refresh: bool = False,
    approve: bool = typer.Option(False, "--approve"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Run a planned and approved dbt run/build selection."""
    _print(
        _service(config).dbt_execute(
            project,
            command,
            selector,
            target=target,
            full_refresh=full_refresh,
            approved=approve,
            plan_id=plan_id,
        )
    )


@app.command("git-publish")
def git_publish(
    project: str,
    action: str,
    plan_id: str = typer.Option(...),
    approve: bool = typer.Option(False, "--approve"),
    branch: str | None = None,
    message: str | None = None,
    path: list[str] | None = typer.Option(None, "--path"),
    title: str | None = None,
    body: str | None = None,
    base: str = "main",
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Perform one planned branch, commit, push, or pull-request action."""
    _print(
        _service(config).git_publish(
            project,
            action,
            approved=approve,
            plan_id=plan_id,
            branch=branch,
            message=message,
            paths=path,
            title=title,
            body=body,
            base=base,
        )
    )


@app.command("serve")
def serve(
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
    transport: str = typer.Option("stdio"),
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_unauthenticated_http: bool = False,
) -> None:
    """Run the MCP server over stdio or authenticated loopback HTTP."""
    run_server(str(config), transport, host, port, allow_unauthenticated_http)


if __name__ == "__main__":
    app()
