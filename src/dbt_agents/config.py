from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import ConfigurationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthConfig(StrictModel):
    mode: Literal["adc", "impersonation", "service_account_file", "workload_identity"] = "adc"
    query_service_account_env: str | None = None
    dbt_service_account_env: str | None = None
    query_credentials_file_env: str = "DBT_AGENTS_QUERY_CREDENTIALS"
    dbt_credentials_file_env: str = "DBT_AGENTS_DBT_CREDENTIALS"


class WarehouseLimits(StrictModel):
    query_max_bytes_billed: int = Field(default=1_000_000_000, gt=0)
    query_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    query_max_rows: int = Field(default=1000, ge=1, le=100_000)
    query_max_result_bytes: int = Field(default=2_000_000, ge=1024)
    query_max_cell_chars: int = Field(default=10_000, ge=32)
    dbt_timeout_seconds: int = Field(default=900, ge=1, le=86_400)
    file_max_bytes: int = Field(default=1_000_000, ge=1024)
    search_max_matches: int = Field(default=200, ge=1, le=10_000)


class WarehouseConfig(StrictModel):
    provider: str = "bigquery"
    project: str
    location: str = "US"
    default_dataset: str | None = None
    read_datasets: list[str]
    dbt_write_datasets: list[str]
    forbidden_write_datasets: list[str] = Field(default_factory=list)
    allow_raw_rows: bool = False
    protected_columns: list[str] = Field(default_factory=list)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @model_validator(mode="after")
    def datasets_do_not_overlap(self) -> WarehouseConfig:
        overlap = set(self.dbt_write_datasets) & set(self.forbidden_write_datasets)
        if overlap:
            raise ValueError(f"dbt write datasets are forbidden: {sorted(overlap)}")
        unknown_writes = set(self.dbt_write_datasets) - set(self.read_datasets)
        if unknown_writes:
            raise ValueError(f"dbt write datasets must also be readable: {sorted(unknown_writes)}")
        return self


class DbtConfig(StrictModel):
    executable: str = "dbt"
    profiles_dir: str = "profiles"
    default_target: str = "dev"
    production_targets: list[str] = Field(default_factory=lambda: ["prod"])
    allowed_commands: list[str] = Field(
        default_factory=lambda: ["deps", "parse", "compile", "list", "test", "run", "build"]
    )
    allow_full_refresh: bool = False
    environment: dict[str, str] = Field(default_factory=dict)
    target_datasets: dict[str, str] = Field(default_factory=dict)

    @field_validator("executable")
    @classmethod
    def executable_is_one_argument(cls, value: str) -> str:
        if not value or any(char.isspace() for char in value):
            raise ValueError("dbt executable must be one executable path/name, not a shell command")
        return value


class ProjectConfig(StrictModel):
    repo_root: Path
    dbt_project_dir: Path
    git_root: Path | None = None
    writable_paths: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    dbt: DbtConfig = Field(default_factory=DbtConfig)
    warehouse: WarehouseConfig
    limits: WarehouseLimits = Field(default_factory=WarehouseLimits)
    audit_dir: Path = Path(".dbt-agents")

    @property
    def resolved_dbt_project_dir(self) -> Path:
        path = self.dbt_project_dir
        return path.resolve() if path.is_absolute() else (self.repo_root / path).resolve()

    @property
    def resolved_profiles_dir(self) -> Path:
        path = Path(self.dbt.profiles_dir)
        return (
            path.resolve()
            if path.is_absolute()
            else (self.resolved_dbt_project_dir / path).resolve()
        )

    @property
    def resolved_git_root(self) -> Path:
        if self.git_root is None:
            return self.resolved_dbt_project_dir
        return (
            self.git_root.resolve()
            if self.git_root.is_absolute()
            else (self.repo_root / self.git_root).resolve()
        )

    @property
    def resolved_audit_dir(self) -> Path:
        path = self.audit_dir
        return path.resolve() if path.is_absolute() else (self.repo_root / path).resolve()

    @model_validator(mode="after")
    def validate_paths(self) -> ProjectConfig:
        self.repo_root = self.repo_root.expanduser().resolve()
        if not self.repo_root.is_dir():
            raise ValueError(f"repo_root is not a directory: {self.repo_root}")
        dbt_dir = self.resolved_dbt_project_dir
        if self.repo_root not in (dbt_dir, *dbt_dir.parents):
            raise ValueError("dbt_project_dir must be inside repo_root")
        if not (dbt_dir / "dbt_project.yml").is_file():
            raise ValueError(f"dbt_project.yml not found in {dbt_dir}")
        git_root = self.resolved_git_root
        if self.repo_root not in (git_root, *git_root.parents):
            raise ValueError("git_root must be inside repo_root")
        if not (git_root / ".git").exists():
            raise ValueError(f"git_root is not a Git checkout: {git_root}")
        invalid_targets = set(self.dbt.target_datasets.values()) - set(
            self.warehouse.dbt_write_datasets
        )
        if invalid_targets:
            raise ValueError(
                f"dbt target datasets are not allowlisted for writes: {sorted(invalid_targets)}"
            )
        return self


class AppConfig(StrictModel):
    version: Literal[1]
    projects: dict[str, ProjectConfig]

    @field_validator("projects")
    @classmethod
    def projects_are_not_empty(cls, value: dict[str, ProjectConfig]) -> dict[str, ProjectConfig]:
        if not value:
            raise ValueError("at least one project is required")
        return value


_ENV_PATTERN_PREFIX = "${"


def _expand_env(value: object) -> object:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str) and _ENV_PATTERN_PREFIX in value:
        return os.path.expandvars(value)
    return value


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file not found: {config_path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"unable to read configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration root must be a mapping")

    raw = _expand_env(raw)
    projects = raw.get("projects", {})
    if isinstance(projects, dict):
        for project in projects.values():
            if isinstance(project, dict) and "repo_root" in project:
                repo_root = Path(project["repo_root"])
                if not repo_root.is_absolute():
                    project["repo_root"] = str((config_path.parent / repo_root).resolve())
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError("invalid configuration", {"errors": exc.errors()}) from exc
