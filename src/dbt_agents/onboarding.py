from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adapters.dbt import DbtAdapter
from .config import AppConfig, load_config
from .doctor import Doctor
from .errors import ApprovalRequired, ConfigurationError, StaleWorkspace
from .utils import run_command, sha256_bytes


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(StrictModel):
    name: str
    locations: list[str] = Field(default_factory=list)
    default_present: bool = False
    status: str = "unresolved"


class OnboardingReport(StrictModel):
    version: int = 1
    repository: dict[str, str]
    dbt: dict[str, Any]
    requirements: dict[str, list[Requirement]]
    questions: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    candidate_paths: list[str] = Field(default_factory=list)


class OnboardingAnswers(StrictModel):
    alias: str
    warehouse_project: str
    source_datasets: list[str]
    read_datasets: list[str]
    dbt_write_datasets: list[str]
    target_datasets: dict[str, str]
    default_target: str = "dev"
    production_targets: list[str] = Field(default_factory=lambda: ["prod"])
    location: str = "US"
    profiles_dir: str = "profiles"
    dbt_environment: dict[str, str] = Field(default_factory=dict)
    auth_mode: str = "adc"
    query_service_account_env: str | None = None
    dbt_service_account_env: str | None = None


_CALL = re.compile(r"\b(?P<kind>var|env_var)\s*\(\s*['\"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['\"]")
_DEFAULT = re.compile(r"\b(?:var|env_var)\s*\(\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*,")
_SKIP_DIRS = {".git", ".dbt-agents", "dbt_packages", "logs", "target", ".venv", "venv"}
_TEXT_SUFFIXES = {".sql", ".yml", ".yaml", ".md", ".jinja", ".sqlx"}
_SECRET_MARKERS = ("secret", "token", "password", "private", "credential")


def inspect_repository(repo_root: Path, dbt_project_dir: Path | None = None) -> OnboardingReport:
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise ConfigurationError(f"repository root is not a directory: {root}")
    dbt_dir = _find_dbt_project(root, dbt_project_dir)
    project = _read_yaml_mapping(dbt_dir / "dbt_project.yml")
    project_vars = project.get("vars", {}) if isinstance(project.get("vars"), dict) else {}
    requirements = _scan_requirements(root, project_vars)
    unresolved = [item.name for item in requirements["vars"] if item.status == "unresolved"]
    environment = [item.name for item in requirements["environment"] if not item.default_present]
    questions: list[str] = []
    if unresolved:
        questions.append("Define or confirm providers for dbt vars: " + ", ".join(unresolved))
    if environment:
        questions.append("Provide non-secret environment settings: " + ", ".join(environment))
    questions.extend(
        [
            "Confirm the warehouse project, source/read datasets, and dbt-owned write datasets.",
            "Confirm the development/CI target mapping and authentication mode.",
        ]
    )
    findings = [
        {
            "code": "unresolved_dbt_var",
            "severity": "error",
            "message": f"dbt var '{item.name}' has no project-level provider",
            "locations": item.locations,
        }
        for item in requirements["vars"]
        if item.status == "unresolved"
    ]
    return OnboardingReport(
        repository={"root": str(root), "dbt_project_dir": str(dbt_dir)},
        dbt={
            "project_name": project.get("name"),
            "profile": project.get("profile"),
            "targets": [],
            "packages_file_present": (dbt_dir / "packages.yml").is_file(),
            "paths": {
                key: project.get(key, [])
                for key in ("model-paths", "analysis-paths", "test-paths", "macro-paths")
            },
        },
        requirements=requirements,
        questions=questions,
        findings=findings,
    )


def load_report(path: Path) -> OnboardingReport:
    return _load_model(path, OnboardingReport, "onboarding report")


def load_answers(path: Path) -> OnboardingAnswers:
    answers = _load_model(path, OnboardingAnswers, "onboarding answers")
    _validate_answers(answers)
    return answers


def write_report(report: OnboardingReport, output: Path) -> None:
    _write_json(output, report.model_dump(mode="json"))


def propose_candidate(report: OnboardingReport, answers: OnboardingAnswers) -> dict[str, Any]:
    _validate_answers(answers)
    root = Path(report.repository["root"]).resolve()
    dbt_dir = Path(report.repository["dbt_project_dir"]).resolve()
    if root not in (dbt_dir, *dbt_dir.parents):
        raise ConfigurationError("report dbt project is outside repository root")
    if not (dbt_dir / "dbt_project.yml").is_file():
        raise ConfigurationError("report dbt_project.yml no longer exists")
    git_root = _find_git_root(root, dbt_dir)
    relative_dbt = dbt_dir.relative_to(root)
    relative_git = git_root.relative_to(root)
    auth: dict[str, Any] = {"mode": answers.auth_mode}
    if answers.auth_mode == "impersonation":
        if not answers.query_service_account_env or not answers.dbt_service_account_env:
            raise ConfigurationError(
                "impersonation requires both identity environment variable names"
            )
        auth.update(
            {
                "query_service_account_env": answers.query_service_account_env,
                "dbt_service_account_env": answers.dbt_service_account_env,
            }
        )
    prefix = str(relative_dbt).rstrip("/")
    directories = ("models", "macros", "tests", "seeds", "snapshots", "analyses", "docs", "specs")
    writable = [f"{prefix}/{directory}/**" for directory in directories]
    writable.extend([f"{prefix}/dbt_project.yml", f"{prefix}/packages.yml"])
    project = {
        "repo_root": str(root),
        "git_root": str(relative_git),
        "dbt_project_dir": str(relative_dbt),
        "audit_dir": str(root / ".dbt-agents" / answers.alias),
        "writable_paths": writable,
        "protected_paths": [
            "**/.env*",
            "**/*credential*",
            "**/*service-account*",
            "**/*-????????????.json",
            "**/*.pem",
            "**/*.key",
            "**/.git/**",
        ],
        "dbt": {
            "profiles_dir": answers.profiles_dir,
            "default_target": answers.default_target,
            "production_targets": answers.production_targets,
            "target_datasets": answers.target_datasets,
            "environment": answers.dbt_environment,
        },
        "warehouse": {
            "provider": "bigquery",
            "project": answers.warehouse_project,
            "location": answers.location,
            "read_datasets": sorted(
                set(answers.read_datasets)
                | set(answers.source_datasets)
                | set(answers.dbt_write_datasets)
            ),
            "dbt_write_datasets": sorted(set(answers.dbt_write_datasets)),
            "forbidden_write_datasets": sorted(set(answers.source_datasets)),
            "auth": auth,
        },
    }
    candidate = {"version": 1, "projects": {answers.alias: project}}
    _validate_candidate(candidate)
    return candidate


def write_candidate(candidate: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")


def readiness(
    candidate_path: Path,
    *,
    compile_project: bool = False,
    with_deps: bool = False,
    live: bool = False,
) -> dict[str, Any]:
    config = load_config(candidate_path)
    checks: list[dict[str, Any]] = []
    for alias, project in sorted(config.projects.items()):
        check = {"project": alias, "checks": []}
        check["checks"].append(
            _check(
                "static_configuration_contract",
                lambda project=project: _static_contract(
                    project.repo_root, project.resolved_dbt_project_dir
                ),
            )
        )
        check["checks"].append(
            _check(
                "dbt_executable", lambda project=project: _dbt_executable(project.dbt.executable)
            )
        )
        check["checks"].append(
            _check("profile", lambda project=project: _profile(project.resolved_profiles_dir))
        )
        if compile_project:
            adapter = DbtAdapter(project)
            if with_deps and (project.resolved_dbt_project_dir / "packages.yml").is_file():
                check["checks"].append(
                    _check("dbt_deps", lambda adapter=adapter: adapter.run("deps"))
                )
            check["checks"].append(
                _check("dbt_parse", lambda adapter=adapter: adapter.run("parse"))
            )
            check["checks"].append(
                _check("dbt_compile", lambda adapter=adapter: adapter.run("compile"))
            )
        if live:
            check["checks"].append(
                _check("live_readiness", lambda project=project: Doctor(project).run(live=True))
            )
        checks.append(check)
    failures = [
        {"project": project["project"], "check": item["name"]}
        for project in checks
        for item in project["checks"]
        if not item["ok"]
    ]
    return {
        "ok": not failures,
        "operation": "onboard_validate",
        "checks": checks,
        "failures": failures,
    }


def registration_preview(active_config: Path, candidate_path: Path) -> dict[str, Any]:
    active_raw = (
        _read_yaml_mapping(active_config)
        if active_config.exists()
        else {"version": 1, "projects": {}}
    )
    candidate = _read_yaml_mapping(candidate_path)
    merged = _merge_candidate(active_raw, candidate)
    candidate_bytes = yaml.safe_dump(merged, sort_keys=False).encode()
    before = active_config.read_text(encoding="utf-8") if active_config.exists() else ""
    after = candidate_bytes.decode()
    return {
        "ok": True,
        "operation": "onboard_register",
        "approval_required": True,
        "active_config_sha256": sha256_bytes(before.encode()),
        "candidate_projects": sorted(candidate.get("projects", {})),
        "diff": "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=str(active_config),
                tofile=str(active_config),
            )
        ),
        "merged": merged,
    }


def register_candidate(
    active_config: Path, candidate_path: Path, expected_sha256: str, approved: bool
) -> dict[str, Any]:
    current = active_config.read_bytes() if active_config.exists() else b""
    if expected_sha256 != sha256_bytes(current):
        raise StaleWorkspace("active configuration changed since preview")
    preview = registration_preview(active_config, candidate_path)
    if not approved:
        raise ApprovalRequired(
            "onboarding registration requires explicit approval", {"preview": preview}
        )
    _validate_candidate(preview["merged"])
    active_config.parent.mkdir(parents=True, exist_ok=True)
    temporary = active_config.with_suffix(active_config.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(preview["merged"], sort_keys=False), encoding="utf-8")
    os.replace(temporary, active_config)
    return {
        "ok": True,
        "operation": "onboard_register",
        "registered_projects": preview["candidate_projects"],
        "restart_required": True,
    }


def _find_dbt_project(root: Path, requested: Path | None) -> Path:
    if requested is not None:
        path = requested.expanduser().resolve()
        if root not in (path, *path.parents):
            raise ConfigurationError("dbt_project_dir must be inside repo_root")
        if not (path / "dbt_project.yml").is_file():
            raise ConfigurationError(f"dbt_project.yml not found in {path}")
        return path
    if (root / "dbt_project.yml").is_file():
        return root
    found = [path.parent for path in root.rglob("dbt_project.yml") if not _skip(path, root)]
    if len(found) != 1:
        raise ConfigurationError(
            "provide --dbt-project-dir when repository has zero or multiple dbt projects",
            {"found": [str(path) for path in found]},
        )
    return found[0]


def _find_git_root(root: Path, dbt_dir: Path) -> Path:
    for path in (dbt_dir, *dbt_dir.parents):
        if (path / ".git").exists():
            if root in (path, *path.parents):
                return path
    raise ConfigurationError("no Git checkout found between dbt project and repository root")


def _scan_requirements(root: Path, project_vars: dict[str, Any]) -> dict[str, list[Requirement]]:
    found: dict[str, dict[str, Requirement]] = {"vars": {}, "environment": {}}
    for path in root.rglob("*"):
        if not path.is_file() or _skip(path, root) or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, start=1):
            for match in _CALL.finditer(line):
                group = "vars" if match.group("kind") == "var" else "environment"
                name = match.group("name")
                item = found[group].setdefault(name, Requirement(name=name))
                item.locations.append(f"{path.relative_to(root)}:{number}")
                item.default_present = item.default_present or bool(_DEFAULT.search(line))
    for name, item in found["vars"].items():
        if name in project_vars:
            item.status = "project_default"
        elif item.default_present:
            item.status = "call_default"
    return {key: [value for _, value in sorted(items.items())] for key, items in found.items()}


def _skip(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part.lower() in _SKIP_DIRS for part in relative.parts):
        return True
    name = path.name.lower()
    return (
        name.startswith(".env")
        or any(marker in name for marker in _SECRET_MARKERS)
        or name.endswith((".pem", ".key", ".p12"))
    )


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"file not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"unable to read YAML: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"YAML root must be a mapping: {path}")
    return value


def _load_model(path: Path, model: type[BaseModel], label: str) -> Any:
    try:
        value = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.suffix == ".json"
            else _read_yaml_mapping(path)
        )
        return model.model_validate(value)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ConfigurationError(f"invalid {label}: {path}") from exc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_answers(answers: OnboardingAnswers) -> None:
    for name in answers.dbt_environment:
        if any(marker in name.lower() for marker in _SECRET_MARKERS):
            raise ConfigurationError(f"dbt_environment cannot include secret-like variable: {name}")


def _validate_candidate(candidate: dict[str, Any]) -> None:
    try:
        AppConfig.model_validate(candidate)
    except ValidationError as exc:
        raise ConfigurationError("invalid onboarding candidate", {"errors": exc.errors()}) from exc


def _merge_candidate(active: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if active.get("version", 1) != 1 or candidate.get("version") != 1:
        raise ConfigurationError("only version: 1 configuration can be registered")
    active_projects = active.get("projects")
    candidate_projects = candidate.get("projects")
    if not isinstance(active_projects, dict) or not isinstance(candidate_projects, dict):
        raise ConfigurationError("active and candidate configurations require projects mappings")
    collisions = sorted(set(active_projects) & set(candidate_projects))
    if collisions:
        raise ConfigurationError("candidate aliases already exist", {"aliases": collisions})
    return {**active, "version": 1, "projects": {**active_projects, **candidate_projects}}


def _check(name: str, callback: Any) -> dict[str, Any]:
    try:
        return {"name": name, "ok": True, "details": callback()}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc), "error_type": type(exc).__name__}


def _dbt_executable(executable: str) -> dict[str, str]:
    resolved = shutil.which(executable)
    if not resolved:
        raise RuntimeError(f"dbt executable not found: {executable}")
    result = run_command([executable, "--version"], cwd=Path.cwd(), timeout=20)
    if result.exit_code:
        raise RuntimeError(result.stderr or "dbt --version failed")
    return {"executable": resolved}


def _profile(profiles_dir: Path) -> dict[str, str]:
    profile = profiles_dir / "profiles.yml"
    if not profile.is_file():
        raise RuntimeError(f"dbt profile does not exist: {profile}")
    return {"profile": str(profile)}


def _static_contract(repo_root: Path, dbt_project_dir: Path) -> dict[str, Any]:
    report = inspect_repository(repo_root, dbt_project_dir)
    unresolved = [finding for finding in report.findings if finding["severity"] == "error"]
    if unresolved:
        raise RuntimeError("; ".join(finding["message"] for finding in unresolved))
    return {
        "required_vars": [item.name for item in report.requirements["vars"]],
        "required_environment": [item.name for item in report.requirements["environment"]],
    }
