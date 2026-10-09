from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from .config import load_config
from .doctor import Doctor
from .iam import BigQueryIam
from .onboarding import (
    inspect_repository,
    load_answers,
    load_report,
    propose_candidate,
    readiness,
    register_candidate,
    registration_preview,
    write_candidate,
    write_report,
)
from .server import run_server
from .service import DbtAgentsService
from .validation import ValidationWorkflow

app = typer.Typer(no_args_is_help=True, help="Policy-enforced dbt and warehouse tools.")
onboard_app = typer.Typer(no_args_is_help=True, help="Safely onboard an unconfigured dbt project.")
app.add_typer(onboard_app, name="onboard")


def _service(config: Path) -> DbtAgentsService:
    return DbtAgentsService(load_config(config))


def _print(result: dict[str, Any]) -> None:
    typer.echo(json.dumps(result, indent=2, sort_keys=True, default=str))
    if result.get("ok") is False:
        raise typer.Exit(1)


def _config_option() -> Path:
    return Path(os.getenv("DBT_AGENTS_CONFIG", "dbt-agents.yml"))


def _onboarding_result(operation: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "operation": operation, "data": data}


@onboard_app.command("inspect")
def onboard_inspect(
    repo_root: Path = typer.Option(..., exists=True, file_okay=False),
    dbt_project_dir: Path | None = typer.Option(None, exists=True, file_okay=False),
    output: Path | None = typer.Option(None, help="Optional JSON report destination"),
) -> None:
    """Inventory one local dbt repository without credentials or network access."""
    report = inspect_repository(repo_root, dbt_project_dir)
    if output is not None:
        write_report(report, output)
    _print(_onboarding_result("onboard_inspect", report.model_dump(mode="json")))


@onboard_app.command("propose")
def onboard_propose(
    report: Path = typer.Option(..., exists=True, dir_okay=False),
    answers: Path = typer.Option(..., exists=True, dir_okay=False),
    output: Path = typer.Option(..., help="Candidate dbt-agents configuration destination"),
) -> None:
    """Generate an inactive candidate configuration from reviewed answers."""
    candidate = propose_candidate(load_report(report), load_answers(answers))
    write_candidate(candidate, output)
    _print(
        _onboarding_result(
            "onboard_propose",
            {"candidate_path": str(output), "projects": sorted(candidate["projects"])},
        )
    )


@onboard_app.command("validate")
def onboard_validate(
    candidate: Path = typer.Option(..., exists=True, dir_okay=False),
    compile_project: bool = typer.Option(False, "--compile", help="Run dbt parse and compile"),
    with_deps: bool = typer.Option(False, "--with-deps", help="Run dbt deps before parse/compile"),
    live: bool = typer.Option(False, "--live", help="Run credential and BigQuery readiness checks"),
) -> None:
    """Validate a candidate from local checks through optional live access."""
    _print(readiness(candidate, compile_project=compile_project, with_deps=with_deps, live=live))


@onboard_app.command("register")
def onboard_register(
    candidate: Path = typer.Option(..., exists=True, dir_okay=False),
    config: Path = typer.Option(_config_option(), help="Active local dbt-agents configuration"),
    expected_sha256: str | None = typer.Option(
        None, help="Hash returned by the registration preview"
    ),
    approve: bool = typer.Option(False, "--approve"),
) -> None:
    """Preview or explicitly register a candidate without replacing existing aliases."""
    preview = registration_preview(config, candidate)
    public_preview = {key: value for key, value in preview.items() if key != "merged"}
    if not approve:
        _print(public_preview)
        return
    if not expected_sha256:
        raise typer.BadParameter("--expected-sha256 from the preview is required with --approve")
    _print(register_candidate(config, candidate, expected_sha256, approved=True))


@app.command("check-config")
def check_config(
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Validate configuration without connecting to the warehouse."""
    value = load_config(config)
    typer.echo(json.dumps({"valid": True, "projects": sorted(value.projects)}, indent=2))


@app.command("doctor")
def doctor(
    project: str,
    live: bool = typer.Option(False, "--live", help="Also call BigQuery read APIs"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Check local configuration, tools, credentials, and optional live access."""
    loaded = load_config(config)
    if project not in loaded.projects:
        raise typer.BadParameter(f"unknown project: {project}")
    _print(Doctor(loaded.projects[project]).run(live=live))


@app.command("iam-plan")
def iam_plan(
    project: str,
    query_identity: str | None = typer.Option(None),
    dbt_identity: str | None = typer.Option(None),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Print the required two-identity BigQuery bindings without applying them."""
    loaded = load_config(config)
    if project not in loaded.projects:
        raise typer.BadParameter(f"unknown project: {project}")
    _print(
        {
            "ok": True,
            **BigQueryIam(loaded.projects[project]).plan(query_identity, dbt_identity),
        }
    )


@app.command("iam-verify")
def iam_verify(
    project: str,
    query_identity: str | None = typer.Option(None),
    dbt_identity: str | None = typer.Option(None),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Inspect project and dataset policies without attempting a negative write."""
    loaded = load_config(config)
    if project not in loaded.projects:
        raise typer.BadParameter(f"unknown project: {project}")
    _print(BigQueryIam(loaded.projects[project]).verify(query_identity, dbt_identity))


@app.command("iam-apply")
def iam_apply(
    project: str,
    query_identity: str = typer.Option(...),
    dbt_identity: str = typer.Option(...),
    approve: bool = typer.Option(False, "--approve"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Idempotently provision the reviewed two-identity BigQuery boundary."""
    loaded = load_config(config)
    if project not in loaded.projects:
        raise typer.BadParameter(f"unknown project: {project}")
    _print(
        BigQueryIam(loaded.projects[project]).apply(
            query_identity=query_identity,
            dbt_identity=dbt_identity,
            approved=approve,
        )
    )


@app.command("project")
def project_get(
    project: str,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    _print(_service(config).project_get(project))


@app.command("project-readiness")
def project_readiness(
    project: str,
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Inspect a configured project's static dbt configuration contract."""
    _print(_service(config).project_readiness(project))


@app.command("projects")
def project_list(
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """List configured dbt-agents project aliases without loading credentials."""
    _print(_service(config).project_list())


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


@app.command("validate")
def validate(
    project: str,
    selector: str | None = None,
    target: str | None = None,
    with_tests: bool = typer.Option(False, "--with-tests"),
    approve: bool = typer.Option(False, "--approve"),
    config: Path = typer.Option(_config_option(), exists=True, dir_okay=False),
) -> None:
    """Run Git status, dbt compile, and optionally approved targeted dbt tests."""
    workflow = ValidationWorkflow(_service(config))
    _print(
        workflow.run(
            project,
            selector=selector,
            target=target,
            with_tests=with_tests,
            approved=approve,
        )
    )


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
