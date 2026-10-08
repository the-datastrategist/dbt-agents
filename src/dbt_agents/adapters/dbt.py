from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import ProjectConfig
from ..errors import ExecutionFailed, PolicyDenied
from ..models import CommandResult
from ..utils import run_command


class DbtAdapter:
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.project_dir = config.resolved_dbt_project_dir

    def inspect(self) -> dict[str, Any]:
        project = self._read_yaml_summary(self.project_dir / "dbt_project.yml")
        packages = self._read_yaml_summary(self.project_dir / "packages.yml", required=False)
        manifest = self._read_json(self.project_dir / "target" / "manifest.json", required=False)
        nodes: list[dict[str, Any]] = []
        if manifest:
            for unique_id, node in manifest.get("nodes", {}).items():
                nodes.append(
                    {
                        "unique_id": unique_id,
                        "name": node.get("name"),
                        "resource_type": node.get("resource_type"),
                        "original_file_path": node.get("original_file_path"),
                        "database": node.get("database"),
                        "schema": node.get("schema"),
                        "depends_on": node.get("depends_on", {}).get("nodes", []),
                        "tags": node.get("tags", []),
                    }
                )
        return {
            "project_dir": str(self.project_dir),
            "project": project,
            "packages": packages,
            "manifest_present": bool(manifest),
            "manifest_metadata": manifest.get("metadata", {}) if manifest else {},
            "nodes": nodes,
            "node_count": len(nodes),
        }

    def resolve_selector(self, selector: str, target: str | None = None) -> list[dict[str, Any]]:
        self._validate_selector(selector)
        result = self._execute(
            "list",
            selector=selector,
            target=target,
            output_json=True,
        )
        if result.exit_code:
            raise ExecutionFailed("dbt selector resolution failed", {"result": result.model_dump()})
        nodes: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("unique_id"):
                nodes.append(value)
        if not nodes:
            raise PolicyDenied("dbt selector resolved to no nodes", {"selector": selector})
        manifest = self._read_json(self.project_dir / "target" / "manifest.json", required=False)
        manifest_nodes = manifest.get("nodes", {})
        for node in nodes:
            resolved = manifest_nodes.get(node["unique_id"], {})
            for field in ("database", "schema", "alias", "relation_name"):
                if field in resolved:
                    node[field] = resolved[field]
        return nodes

    def run(
        self,
        command: str,
        *,
        selector: str | None = None,
        target: str | None = None,
        full_refresh: bool = False,
    ) -> dict[str, Any]:
        result = self._execute(
            command,
            selector=selector,
            target=target,
            full_refresh=full_refresh,
        )
        artifacts = self.artifacts()
        return {"command": result.model_dump(), "artifacts": artifacts}

    def artifacts(self) -> dict[str, Any]:
        target_dir = self.project_dir / "target"
        result: dict[str, Any] = {}
        for filename in ("run_results.json", "sources.json", "manifest.json", "catalog.json"):
            value = self._read_json(target_dir / filename, required=False)
            if not value:
                continue
            if filename == "run_results.json":
                result[filename] = {
                    "metadata": value.get("metadata", {}),
                    "elapsed_time": value.get("elapsed_time"),
                    "results": [
                        {
                            "unique_id": row.get("unique_id"),
                            "status": row.get("status"),
                            "message": row.get("message"),
                            "execution_time": row.get("execution_time"),
                        }
                        for row in value.get("results", [])
                    ],
                }
            else:
                result[filename] = {"metadata": value.get("metadata", {})}
        return result

    def target_dataset(self, target: str | None) -> str:
        selected = target or self.config.dbt.default_target
        dataset = self.config.dbt.target_datasets.get(selected)
        if not dataset:
            raise PolicyDenied(
                "dbt target has no explicit target_datasets mapping",
                {"target": selected},
            )
        if dataset not in self.config.warehouse.dbt_write_datasets:
            raise PolicyDenied("dbt target dataset is not write-allowlisted", {"dataset": dataset})
        if dataset in self.config.warehouse.forbidden_write_datasets:
            raise PolicyDenied("dbt target dataset is explicitly forbidden", {"dataset": dataset})
        return dataset

    def _execute(
        self,
        command: str,
        *,
        selector: str | None = None,
        target: str | None = None,
        full_refresh: bool = False,
        output_json: bool = False,
    ) -> CommandResult:
        if command not in self.config.dbt.allowed_commands:
            raise PolicyDenied("dbt command is not allowlisted", {"command": command})
        if selector:
            self._validate_selector(selector)
        if full_refresh and not self.config.dbt.allow_full_refresh:
            raise PolicyDenied("dbt full refresh is disabled")
        selected_target = target or self.config.dbt.default_target
        if command in {"test", "run", "build"}:
            dataset = self.target_dataset(selected_target)
        else:
            dataset = self.config.dbt.target_datasets.get(selected_target)

        argv = [
            self.config.dbt.executable,
            command,
            "--project-dir",
            str(self.project_dir),
            "--profiles-dir",
            str(self.config.resolved_profiles_dir),
            "--target",
            selected_target,
            "--no-use-colors",
        ]
        if selector:
            argv.extend(["--select", selector])
        if full_refresh:
            argv.append("--full-refresh")
        if output_json:
            argv.extend(["--output", "json", "--quiet"])

        environment = dict(self.config.dbt.environment)
        environment.setdefault("DBT_GCP_PROJECT_ID", self.config.warehouse.project)
        if dataset:
            environment["DBT_TARGET_DATASET"] = dataset
        environment.update(self._auth_environment(command))
        return run_command(
            argv,
            cwd=self.project_dir,
            timeout=self.config.limits.dbt_timeout_seconds,
            env=environment,
        )

    def _auth_environment(self, command: str) -> dict[str, str]:
        auth = self.config.warehouse.auth
        if auth.mode == "service_account_file":
            value = os.getenv(auth.dbt_credentials_file_env)
            if not value:
                raise PolicyDenied(
                    f"{auth.dbt_credentials_file_env} is required for dbt service-account auth"
                )
            return {"GOOGLE_APPLICATION_CREDENTIALS": value}
        if auth.mode == "impersonation":
            if not auth.dbt_service_account_env:
                raise PolicyDenied("dbt_service_account_env is required for impersonation")
            target = os.getenv(auth.dbt_service_account_env)
            if not target:
                raise PolicyDenied(f"{auth.dbt_service_account_env} is not set")
            profiles_path = self.config.resolved_profiles_dir / "profiles.yml"
            try:
                profiles_text = profiles_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PolicyDenied("unable to inspect dbt profile for impersonation") from exc
            variable = "DBT_AGENTS_DBT_IMPERSONATE_SERVICE_ACCOUNT"
            if variable not in profiles_text:
                raise PolicyDenied(
                    "dbt profile must set impersonate_service_account from "
                    f"env_var('{variable}') before impersonation mode can run"
                )
            return {variable: target}
        # dbt-bigquery obtains ADC/workload identity from the runtime.
        return {}

    @staticmethod
    def _validate_selector(selector: str) -> None:
        if not selector or len(selector) > 500 or "\x00" in selector or "\n" in selector:
            raise PolicyDenied("invalid dbt selector")
        if selector.startswith("-"):
            raise PolicyDenied("dbt selector cannot begin with a flag")

    @staticmethod
    def _read_json(path: Path, *, required: bool) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if required:
                raise
            return {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _read_yaml_summary(path: Path, *, required: bool = True) -> dict[str, Any]:
        import yaml

        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if required:
                raise
            return {}
        if not isinstance(value, dict):
            return {}
        allowed = {"name", "version", "config-version", "profile", "model-paths", "packages"}
        return {key: value[key] for key in allowed if key in value}
