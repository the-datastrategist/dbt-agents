from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .adapters.dbt import DbtAdapter
from .adapters.repository import RepositoryAdapter
from .config import ProjectConfig
from .providers.bigquery import BigQueryProvider
from .utils import run_command


class Doctor:
    """Run deterministic local and live readiness checks without mutating state."""

    def __init__(self, config: ProjectConfig):
        self.config = config

    def run(self, *, live: bool = False) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        self._record(checks, "repository", True, self._repository)
        self._record(checks, "git", True, self._git)
        self._record(checks, "dbt", True, self._dbt)
        self._record(checks, "profile", True, self._profile)
        self._record(checks, "targets", True, self._targets)
        self._record(checks, "authentication", True, self._authentication)
        self._record(checks, "optional_tools", False, self._optional_tools)
        if live:
            self._record(checks, "bigquery", True, self._bigquery)
        failed = [check["name"] for check in checks if check["required"] and not check["ok"]]
        return {
            "ok": not failed,
            "mode": "live" if live else "offline",
            "failed_required_checks": failed,
            "checks": checks,
        }

    def _repository(self) -> dict[str, Any]:
        return {
            "repo_root": str(self.config.repo_root),
            "dbt_project_dir": str(self.config.resolved_dbt_project_dir),
            "git_root": str(self.config.resolved_git_root),
        }

    def _git(self) -> dict[str, Any]:
        status = RepositoryAdapter(self.config).status(include_diff=False)
        return {"branch": status["branch"], "changed_paths": len(status["status"])}

    def _dbt(self) -> dict[str, Any]:
        executable = shutil.which(self.config.dbt.executable)
        if not executable:
            raise RuntimeError(f"dbt executable not found: {self.config.dbt.executable}")
        result = run_command(
            [self.config.dbt.executable, "--version"],
            cwd=self.config.resolved_dbt_project_dir,
            timeout=20,
        )
        if result.exit_code:
            raise RuntimeError(result.stderr or "dbt --version failed")
        return {"executable": executable, "version": result.stdout.splitlines()[0:4]}

    def _profile(self) -> dict[str, Any]:
        profile = self.config.resolved_profiles_dir / "profiles.yml"
        if not profile.is_file():
            raise RuntimeError(f"dbt profile does not exist: {profile}")
        details: dict[str, Any] = {"path": str(profile)}
        if self.config.warehouse.auth.mode == "impersonation":
            marker = "DBT_AGENTS_DBT_IMPERSONATE_SERVICE_ACCOUNT"
            if marker not in profile.read_text(encoding="utf-8"):
                raise RuntimeError(f"dbt profile does not consume {marker}")
            details["impersonation_marker"] = marker
        return details

    def _targets(self) -> dict[str, Any]:
        adapter = DbtAdapter(self.config)
        resolved = {
            target: adapter.target_dataset(target)
            for target in sorted(self.config.dbt.target_datasets)
        }
        if self.config.dbt.default_target not in resolved:
            raise RuntimeError("default dbt target has no dataset mapping")
        return {"targets": resolved, "production_targets": self.config.dbt.production_targets}

    def _authentication(self) -> dict[str, Any]:
        auth = self.config.warehouse.auth
        details: dict[str, Any] = {"mode": auth.mode, "assurance": "strong"}
        if auth.mode == "adc":
            import google.auth

            credentials, detected_project = google.auth.default()
            details.update(
                {
                    "credential_type": type(credentials).__name__,
                    "detected_project": detected_project,
                    "assurance": "lower",
                    "warning": "ADC may be broader than the configured two-identity boundary.",
                }
            )
        elif auth.mode == "impersonation":
            required = [auth.query_service_account_env, auth.dbt_service_account_env]
            missing = [name for name in required if not name or not os.getenv(name)]
            if missing:
                raise RuntimeError(f"missing impersonation environment variables: {missing}")
            details["query_identity"] = os.getenv(auth.query_service_account_env or "")
            details["dbt_identity"] = os.getenv(auth.dbt_service_account_env or "")
        elif auth.mode == "service_account_file":
            query_path = os.getenv(auth.query_credentials_file_env)
            dbt_path = os.getenv(auth.dbt_credentials_file_env)
            if not query_path or not dbt_path:
                raise RuntimeError("both query and dbt credential paths are required")
            if Path(query_path).resolve() == Path(dbt_path).resolve():
                raise RuntimeError("query and dbt credential files must be distinct")
            for path in (query_path, dbt_path):
                if not Path(path).is_file():
                    raise RuntimeError(f"credential file does not exist: {path}")
            details["credential_files_distinct"] = True
        else:
            details["runtime_credentials"] = "workload identity / ADC chain"
        return details

    @staticmethod
    def _optional_tools() -> dict[str, Any]:
        return {name: shutil.which(name) for name in ("gcloud", "bq", "gh", "docker")}

    def _bigquery(self) -> dict[str, Any]:
        return BigQueryProvider(self.config).health_check()

    @staticmethod
    def _record(
        checks: list[dict[str, Any]],
        name: str,
        required: bool,
        function: Callable[[], dict[str, Any]],
    ) -> None:
        try:
            details = function()
            checks.append({"name": name, "required": required, "ok": True, "details": details})
        except Exception as exc:
            checks.append(
                {
                    "name": name,
                    "required": required,
                    "ok": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
