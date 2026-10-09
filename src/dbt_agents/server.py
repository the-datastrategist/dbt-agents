from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
from typing import Any

from .config import load_config
from .service import DbtAgentsService


def create_server(config_path: str | Path, *, host: str = "127.0.0.1", port: int = 8765) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise RuntimeError("install dbt-agents[mcp] to run the MCP server") from exc

    service = DbtAgentsService(load_config(config_path))
    server = FastMCP(
        "dbt-agents",
        instructions=(
            "If the project alias is unknown, call project_list first and pass a returned id to "
            "project_get. Use read tools first. Before any write, call change_plan with the exact "
            "arguments, "
            "show the plan to the user, and obtain confirmation. Direct data mutation is "
            "forbidden; "
            "warehouse_query is read-only and dbt_execute is the only warehouse write path."
        ),
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )

    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    local_write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
    warehouse_write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )

    @server.tool(title="List configured dbt projects", annotations=readonly, structured_output=True)
    def project_list() -> dict[str, Any]:
        """Discover project aliases, then pass a returned id to project-scoped tools."""
        return service.project_list()

    @server.tool(title="Get configured dbt project", annotations=readonly, structured_output=True)
    def project_get(project: str) -> dict[str, Any]:
        """Use this first to inspect project paths, warehouse boundaries, auth, and limits."""
        return service.project_get(project)

    @server.tool(title="Search repository", annotations=readonly, structured_output=True)
    def repo_search(
        project: str, query: str, path: str = ".", regex: bool = False
    ) -> dict[str, Any]:
        """Search bounded repository text while excluding protected files."""
        return service.repo_search(project, query, path, regex)

    @server.tool(title="Read repository file", annotations=readonly, structured_output=True)
    def repo_read(
        project: str, path: str, start_line: int = 1, end_line: int | None = None
    ) -> dict[str, Any]:
        """Read a bounded UTF-8 repository file and return its SHA-256 for guarded edits."""
        return service.repo_read(project, path, start_line, end_line)

    @server.tool(title="Inspect Git status", annotations=readonly, structured_output=True)
    def repo_status(project: str, include_diff: bool = True) -> dict[str, Any]:
        """Inspect branch, status, and optionally the current unstaged diff without changing Git."""
        return service.repo_status(project, include_diff)

    @server.tool(title="Inspect dbt project", annotations=readonly, structured_output=True)
    def dbt_inspect(project: str) -> dict[str, Any]:
        """Inspect dbt project metadata and the current manifest lineage when available."""
        return service.dbt_inspect(project)

    @server.tool(title="Compile dbt resources", annotations=readonly, structured_output=True)
    def dbt_compile(
        project: str, selector: str | None = None, target: str | None = None
    ) -> dict[str, Any]:
        """Compile allowlisted dbt resources without intentionally changing warehouse relations."""
        return service.dbt_compile(project, selector, target)

    @server.tool(title="Describe warehouse relation", annotations=readonly, structured_output=True)
    def warehouse_describe(project: str, relation: str) -> dict[str, Any]:
        """Read schema and storage metadata for one allowlisted warehouse relation."""
        return service.warehouse_describe(project, relation)

    @server.tool(
        title="Run read-only warehouse query", annotations=readonly, structured_output=True
    )
    def warehouse_query(project: str, sql: str, dry_run_only: bool = False) -> dict[str, Any]:
        """Dry-run then execute one bounded read-only query under the query identity."""
        return service.warehouse_query(project, sql, dry_run_only)

    @server.tool(title="Create exact change plan", annotations=readonly, structured_output=True)
    def change_plan(project: str, action: str, details: dict[str, Any]) -> dict[str, Any]:
        """Create a short-lived exact plan before calling any write tool."""
        return service.change_plan(project, action, details)

    @server.tool(
        title="Apply guarded repository edit", annotations=local_write, structured_output=True
    )
    def repo_apply_patch(
        project: str,
        path: str,
        content: str,
        expected_sha256: str | None,
        plan_id: str,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Replace one allowlisted file after an exact change plan and explicit user approval."""
        return service.repo_apply_patch(
            project, path, content, expected_sha256, approved=approved, plan_id=plan_id
        )

    @server.tool(title="Run dbt tests", annotations=warehouse_write, structured_output=True)
    def dbt_test(
        project: str,
        selector: str,
        plan_id: str,
        target: str | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Run selected dbt tests after preflight, an exact plan, and explicit approval."""
        return service.dbt_test(
            project, selector, target=target, approved=approved, plan_id=plan_id
        )

    @server.tool(
        title="Run or build dbt resources", annotations=warehouse_write, structured_output=True
    )
    def dbt_execute(
        project: str,
        command: str,
        selector: str,
        plan_id: str,
        target: str | None = None,
        full_refresh: bool = False,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Run/build selected dbt nodes after exact planning and approval."""
        return service.dbt_execute(
            project,
            command,
            selector,
            target=target,
            full_refresh=full_refresh,
            approved=approved,
            plan_id=plan_id,
        )

    @server.tool(title="Publish Git change", annotations=warehouse_write, structured_output=True)
    def git_publish(
        project: str,
        action: str,
        plan_id: str,
        approved: bool = False,
        branch: str | None = None,
        message: str | None = None,
        paths: list[str] | None = None,
        title: str | None = None,
        body: str | None = None,
        base: str = "main",
    ) -> dict[str, Any]:
        """Create a branch/commit/push/PR as a separately planned and approved Git operation."""
        return service.git_publish(
            project,
            action,
            approved=approved,
            plan_id=plan_id,
            branch=branch,
            message=message,
            paths=paths,
            title=title,
            body=body,
            base=base,
        )

    return server


class BearerAuthMiddleware:
    def __init__(self, app: Any, token: str):
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        expected = b"Bearer " + self.token
        if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)


def run_server(
    config: str,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_unauthenticated_http: bool = False,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("v1 HTTP server must bind to loopback")
    server = create_server(config, host=host, port=port)
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise RuntimeError("transport must be stdio or streamable-http")
    token = os.getenv("DBT_AGENTS_HTTP_TOKEN")
    if not token and not allow_unauthenticated_http:
        raise RuntimeError(
            "DBT_AGENTS_HTTP_TOKEN is required for HTTP; use a random value "
            "and configure the client bearer token"
        )
    app: Any = server.streamable_http_app()
    if token:
        app = BearerAuthMiddleware(app, token)
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is required for streamable HTTP") from exc
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the dbt-agents MCP server")
    parser.add_argument("--config", default=os.getenv("DBT_AGENTS_CONFIG", "dbt-agents.yml"))
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-unauthenticated-http", action="store_true")
    args = parser.parse_args()
    run_server(
        args.config,
        args.transport,
        args.host,
        args.port,
        args.allow_unauthenticated_http,
    )


if __name__ == "__main__":
    main()
