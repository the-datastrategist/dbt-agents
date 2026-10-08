from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

from .adapters.dbt import DbtAdapter
from .adapters.repository import RepositoryAdapter
from .audit import AuditLogger
from .config import AppConfig, ProjectConfig
from .errors import DbtAgentsError, PolicyDenied
from .models import ApprovalLevel, OperationResult, PolicyDecision
from .plans import PlanStore
from .policy import PolicyEngine
from .providers.bigquery import BigQueryProvider


class ProjectRuntime:
    def __init__(self, name: str, config: ProjectConfig):
        self.name = name
        self.config = config
        self.policy = PolicyEngine(config)
        self._assert_distinct_runtime_identities()
        self.repo = RepositoryAdapter(config)
        self.dbt = DbtAdapter(config)
        if config.warehouse.provider != "bigquery":
            raise PolicyDenied(
                "warehouse provider is not implemented",
                {"provider": config.warehouse.provider},
            )
        self.warehouse = BigQueryProvider(config)
        self.audit = AuditLogger(config.resolved_audit_dir)
        self.plans = PlanStore(config.resolved_audit_dir, DbtAgentsService.PLAN_TTL)

    def _assert_distinct_runtime_identities(self) -> None:
        auth = self.config.warehouse.auth
        if auth.mode == "service_account_file":
            query_file = os.getenv(auth.query_credentials_file_env)
            dbt_file = os.getenv(auth.dbt_credentials_file_env)
            same_file = (
                query_file
                and dbt_file
                and os.path.realpath(query_file) == os.path.realpath(dbt_file)
            )
            if same_file:
                raise PolicyDenied("query and dbt identities must use different credential files")
        if auth.mode == "impersonation":
            query_account = (
                os.getenv(auth.query_service_account_env)
                if auth.query_service_account_env
                else None
            )
            dbt_account = (
                os.getenv(auth.dbt_service_account_env) if auth.dbt_service_account_env else None
            )
            if query_account and dbt_account and query_account == dbt_account:
                raise PolicyDenied("query and dbt identities must use different service accounts")


class DbtAgentsService:
    PLAN_TTL = timedelta(minutes=30)

    def __init__(self, config: AppConfig):
        self.config = config
        self._runtimes: dict[str, ProjectRuntime] = {}

    def project_get(self, project: str) -> dict[str, Any]:
        runtime = self._runtime(project)
        return self._call(
            runtime,
            "project_get",
            PolicyEngine.allow(ApprovalLevel.INSPECT, "PROJECT-READ"),
            {},
            lambda: {
                "name": project,
                "repo_root": str(runtime.config.repo_root),
                "git_root": str(runtime.config.resolved_git_root),
                "dbt_project_dir": str(runtime.config.resolved_dbt_project_dir),
                "dbt_default_target": runtime.config.dbt.default_target,
                "dbt_production_targets": runtime.config.dbt.production_targets,
                "warehouse": runtime.warehouse.capabilities(),
                "auth_mode": runtime.config.warehouse.auth.mode,
            },
        )

    def repo_read(
        self, project: str, path: str, start_line: int = 1, end_line: int | None = None
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        return self._call(
            runtime,
            "repo_read",
            PolicyEngine.allow(ApprovalLevel.INSPECT, "FS-READ"),
            {"path": path, "start_line": start_line, "end_line": end_line},
            lambda: runtime.repo.read(path, start_line, end_line),
        )

    def repo_search(
        self, project: str, query: str, path: str = ".", regex: bool = False
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        return self._call(
            runtime,
            "repo_search",
            PolicyEngine.allow(ApprovalLevel.INSPECT, "FS-SEARCH"),
            {"query": query, "path": path, "regex": regex},
            lambda: runtime.repo.search(query, path, regex=regex),
        )

    def repo_status(self, project: str, include_diff: bool = True) -> dict[str, Any]:
        runtime = self._runtime(project)
        return self._call(
            runtime,
            "repo_status",
            PolicyEngine.allow(ApprovalLevel.INSPECT, "GIT-READ"),
            {"include_diff": include_diff},
            lambda: runtime.repo.status(include_diff),
        )

    def dbt_inspect(self, project: str) -> dict[str, Any]:
        runtime = self._runtime(project)
        return self._call(
            runtime,
            "dbt_inspect",
            PolicyEngine.allow(ApprovalLevel.INSPECT, "DBT-ARTIFACT-READ"),
            {},
            runtime.dbt.inspect,
        )

    def dbt_compile(
        self, project: str, selector: str | None = None, target: str | None = None
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        return self._call(
            runtime,
            "dbt_compile",
            PolicyEngine.allow(ApprovalLevel.BOUNDED_COMPUTE, "DBT-COMPILE"),
            {"selector": selector, "target": target},
            lambda: runtime.dbt.run("compile", selector=selector, target=target),
        )

    def dbt_test(
        self,
        project: str,
        selector: str,
        *,
        target: str | None,
        approved: bool,
        plan_id: str,
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        try:
            decision = PolicyEngine.require_approval(
                approved,
                self._dbt_level(runtime, target),
                ["dbt tests may execute hooks or persist audit relations"],
                "DBT-TEST-APPROVAL",
            )
            self._consume_plan(
                plan_id, project, "dbt_test", {"selector": selector, "target": target}
            )
        except DbtAgentsError as exc:
            return self._error(runtime, "dbt_test", exc)
        return self._call(
            runtime,
            "dbt_test",
            decision,
            {"selector": selector, "target": target},
            lambda: self._run_dbt_with_resolution(runtime, "test", selector, target, False),
        )

    def dbt_execute(
        self,
        project: str,
        command: str,
        selector: str,
        *,
        target: str | None,
        full_refresh: bool,
        approved: bool,
        plan_id: str,
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        if command not in {"run", "build"}:
            return self._error(
                runtime, "dbt_execute", PolicyDenied("only dbt run/build are executable")
            )
        details = {
            "command": command,
            "selector": selector,
            "target": target,
            "full_refresh": full_refresh,
        }
        try:
            level = self._dbt_level(runtime, target)
            decision = PolicyEngine.require_approval(
                approved,
                level,
                ["dbt will create or replace relations in an allowlisted target dataset"],
                "DBT-WRITE-APPROVAL",
            )
            self._consume_plan(plan_id, project, "dbt_execute", details)
        except DbtAgentsError as exc:
            return self._error(runtime, "dbt_execute", exc)
        return self._call(
            runtime,
            "dbt_execute",
            decision,
            details,
            lambda: self._run_dbt_with_resolution(runtime, command, selector, target, full_refresh),
        )

    def warehouse_describe(self, project: str, relation: str) -> dict[str, Any]:
        runtime = self._runtime(project)
        return self._call(
            runtime,
            "warehouse_describe",
            PolicyEngine.allow(ApprovalLevel.INSPECT, "BQ-METADATA-READ"),
            {"relation": relation},
            lambda: runtime.warehouse.describe_relation(relation),
        )

    def warehouse_query(self, project: str, sql: str, dry_run_only: bool = False) -> dict[str, Any]:
        runtime = self._runtime(project)
        operation = "warehouse_dry_run" if dry_run_only else "warehouse_query"
        return self._call(
            runtime,
            operation,
            PolicyEngine.allow(ApprovalLevel.BOUNDED_COMPUTE, "BQ-READ-ONLY", "BQ-BUDGET"),
            {"sql_sha256": hashlib.sha256(sql.encode()).hexdigest(), "dry_run": dry_run_only},
            lambda: runtime.warehouse.dry_run(sql)
            if dry_run_only
            else runtime.warehouse.execute_read(sql),
        )

    def change_plan(
        self,
        project: str,
        action: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        allowed_actions = {"repo_apply_patch", "dbt_test", "dbt_execute", "git_publish"}
        if action not in allowed_actions:
            return self._error(
                runtime,
                "change_plan",
                PolicyDenied("unsupported planned action", {"action": action}),
            )
        normalized = json.loads(json.dumps(details, sort_keys=True, default=str))
        try:
            record = runtime.plans.create(project, action, normalized)
        except OSError:
            return self._error(
                runtime,
                "change_plan",
                DbtAgentsError("state_unavailable", "unable to persist change plan"),
            )
        return self._call(
            runtime,
            "change_plan",
            PolicyEngine.allow(ApprovalLevel.INSPECT, "PLAN-CREATE"),
            {"action": action, "details": normalized},
            lambda: {
                "plan_id": record["plan_id"],
                "action": action,
                "details": normalized,
                "expires_at": record["expires_at"],
                "next_step": (
                    "Review the exact plan, then call the matching write tool with approved=true."
                ),
            },
        )

    def repo_apply_patch(
        self,
        project: str,
        path: str,
        content: str,
        expected_sha256: str | None,
        *,
        approved: bool,
        plan_id: str,
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        details = {"path": path, "expected_sha256": expected_sha256}
        try:
            decision = PolicyEngine.require_approval(
                approved,
                ApprovalLevel.LOCAL_CHANGE,
                [f"write local repository file {path}"],
                "FS-WRITE-APPROVAL",
                "FS-HASH-GUARD",
            )
            self._consume_plan(plan_id, project, "repo_apply_patch", details)
        except DbtAgentsError as exc:
            return self._error(runtime, "repo_apply_patch", exc)
        return self._call(
            runtime,
            "repo_apply_patch",
            decision,
            details,
            lambda: runtime.repo.write(path, content, expected_sha256),
        )

    def git_publish(
        self,
        project: str,
        action: str,
        *,
        approved: bool,
        plan_id: str,
        branch: str | None = None,
        message: str | None = None,
        paths: list[str] | None = None,
        title: str | None = None,
        body: str | None = None,
        base: str = "main",
    ) -> dict[str, Any]:
        runtime = self._runtime(project)
        details = {
            "action": action,
            "branch": branch,
            "message": message,
            "paths": paths,
            "title": title,
            "body": body,
            "base": base,
        }
        try:
            level = (
                ApprovalLevel.REMOTE_OR_PRODUCTION
                if action in {"push", "pr"}
                else ApprovalLevel.LOCAL_CHANGE
            )
            decision = PolicyEngine.require_approval(
                approved, level, [f"perform Git action {action}"], "GIT-WRITE-APPROVAL"
            )
            self._consume_plan(plan_id, project, "git_publish", details)
        except DbtAgentsError as exc:
            return self._error(runtime, "git_publish", exc)
        return self._call(
            runtime,
            "git_publish",
            decision,
            details,
            lambda: runtime.repo.publish(
                action,
                branch=branch,
                message=message,
                paths=paths,
                title=title,
                body=body,
                base=base,
            ),
        )

    def _run_dbt_with_resolution(
        self,
        runtime: ProjectRuntime,
        command: str,
        selector: str,
        target: str | None,
        full_refresh: bool,
    ) -> dict[str, Any]:
        dataset = runtime.dbt.target_dataset(target)
        nodes = runtime.dbt.resolve_selector(selector, target)
        invalid_nodes = [
            {
                "unique_id": node.get("unique_id"),
                "database": node.get("database"),
                "schema": node.get("schema"),
            }
            for node in nodes
            if node.get("database") not in (None, runtime.config.warehouse.project)
            or node.get("schema") not in runtime.config.warehouse.dbt_write_datasets
        ]
        if invalid_nodes:
            raise PolicyDenied(
                "resolved dbt nodes target resources outside the write allowlist",
                {"nodes": invalid_nodes},
            )
        execution = runtime.dbt.run(
            command,
            selector=selector,
            target=target,
            full_refresh=full_refresh,
        )
        execution["preflight"] = {
            "resolved_nodes": [
                {
                    "unique_id": node.get("unique_id"),
                    "name": node.get("name"),
                    "resource_type": node.get("resource_type"),
                }
                for node in nodes
            ],
            "target": target or runtime.config.dbt.default_target,
            "target_dataset": dataset,
        }
        return execution

    def _dbt_level(self, runtime: ProjectRuntime, target: str | None) -> ApprovalLevel:
        selected = target or runtime.config.dbt.default_target
        return (
            ApprovalLevel.REMOTE_OR_PRODUCTION
            if selected in runtime.config.dbt.production_targets
            else ApprovalLevel.LOCAL_CHANGE
        )

    def _consume_plan(
        self, plan_id: str, project: str, action: str, details: dict[str, Any]
    ) -> None:
        normalized = json.loads(json.dumps(details, sort_keys=True, default=str))
        self._runtime(project).plans.consume(plan_id, project, action, normalized)

    def _runtime(self, project: str) -> ProjectRuntime:
        if project not in self.config.projects:
            raise PolicyDenied("unknown project", {"project": project})
        if project not in self._runtimes:
            self._runtimes[project] = ProjectRuntime(project, self.config.projects[project])
        return self._runtimes[project]

    def _call(
        self,
        runtime: ProjectRuntime,
        operation: str,
        decision: PolicyDecision,
        parameters: dict[str, Any],
        function: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        start = time.monotonic()
        try:
            data = function()
            duration = int((time.monotonic() - start) * 1000)
            result = OperationResult(
                project=runtime.name,
                operation=operation,
                policy=decision,
                duration_ms=duration,
                data=data,
                truncated=bool(data.get("truncated", False)),
            ).model_dump(mode="json")
            payload = {"ok": True, **result}
            runtime.audit.write(
                {
                    "operation_id": result["operation_id"],
                    "project": runtime.name,
                    "operation": operation,
                    "parameters": parameters,
                    "policy": decision.model_dump(mode="json"),
                    "duration_ms": duration,
                    "status": "success",
                }
            )
            return payload
        except DbtAgentsError as exc:
            return self._error(runtime, operation, exc, parameters, decision)
        except Exception as exc:
            return self._error(
                runtime,
                operation,
                DbtAgentsError("internal_error", "operation failed", {"type": type(exc).__name__}),
                parameters,
                decision,
            )

    def _error(
        self,
        runtime: ProjectRuntime,
        operation: str,
        error: DbtAgentsError,
        parameters: dict[str, Any] | None = None,
        decision: PolicyDecision | None = None,
    ) -> dict[str, Any]:
        operation_id = str(uuid4())
        payload = {
            "ok": False,
            "operation_id": operation_id,
            "project": runtime.name,
            "operation": operation,
            **error.as_dict(),
        }
        try:
            runtime.audit.write(
                {
                    "operation_id": operation_id,
                    "project": runtime.name,
                    "operation": operation,
                    "parameters": parameters or {},
                    "policy": decision.model_dump(mode="json") if decision else None,
                    "status": "error",
                    "error": error.as_dict(),
                }
            )
        except OSError:
            payload.setdefault("details", {})["audit"] = "audit log could not be written"
        return payload
