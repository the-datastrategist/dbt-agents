from __future__ import annotations

import json
from pathlib import Path

import pytest

from dbt_agents.errors import StaleWorkspace
from dbt_agents.onboarding import (
    OnboardingAnswers,
    inspect_repository,
    propose_candidate,
    register_candidate,
    registration_preview,
)


def test_inspection_finds_unresolved_vars_without_reading_env(project_root: Path) -> None:
    dbt = project_root / "dbt"
    (dbt / "analyses").mkdir()
    (dbt / "analyses" / "contract.sql").write_text(
        "{% set project = var('source_project') %}\n"
        "{% set dataset = env_var('DBT_SOURCE_DATASET') %}\n"
        "select '{{ project }}'\n",
        encoding="utf-8",
    )
    (dbt / ".env").write_text("DBT_SOURCE_DATASET=must-not-be-read\n", encoding="utf-8")

    report = inspect_repository(project_root)

    assert [item.model_dump() for item in report.requirements["vars"]] == [
        {
            "name": "source_project",
            "locations": ["dbt/analyses/contract.sql:1"],
            "default_present": False,
            "status": "unresolved",
        }
    ]
    assert [item.model_dump() for item in report.requirements["environment"]] == [
        {
            "name": "DBT_SOURCE_DATASET",
            "locations": ["dbt/analyses/contract.sql:2"],
            "default_present": False,
            "status": "unresolved",
        }
    ]
    assert report.findings[0]["code"] == "unresolved_dbt_var"


def test_inspection_recognizes_project_level_var_default(project_root: Path) -> None:
    project = project_root / "dbt" / "dbt_project.yml"
    project.write_text(
        "name: fixture\nversion: 1.0.0\nconfig-version: 2\nprofile: fixture\n"
        "vars:\n  source_project: example\n",
        encoding="utf-8",
    )
    (project_root / "dbt" / "models" / "model.sql").write_text(
        "select '{{ var('source_project') }}'\n", encoding="utf-8"
    )

    report = inspect_repository(project_root)

    assert report.requirements["vars"][0].status == "project_default"
    assert report.findings == []


def test_candidate_is_safe_and_registers_with_hash_guard(
    project_root: Path, tmp_path: Path
) -> None:
    report = inspect_repository(project_root)
    candidate = propose_candidate(
        report,
        OnboardingAnswers(
            alias="fixture",
            warehouse_project="example-project",
            read_datasets=["source"],
            dbt_write_datasets=["dbt_dev"],
            target_datasets={"dev": "dbt_dev"},
            dbt_environment={"DBT_SOURCE_PROJECT": "example-project"},
        ),
    )
    config = tmp_path / "dbt-agents.yml"
    candidate_path = tmp_path / "candidate.yml"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    preview = registration_preview(config, candidate_path)
    assert preview["approval_required"] is True
    assert "fixture" in preview["diff"]
    result = register_candidate(
        config, candidate_path, preview["active_config_sha256"], approved=True
    )

    assert result["ok"] is True
    assert "fixture" in config.read_text(encoding="utf-8")
    with pytest.raises(StaleWorkspace):
        register_candidate(config, candidate_path, preview["active_config_sha256"], approved=True)


def test_candidate_rejects_secret_like_environment_names(project_root: Path) -> None:
    report = inspect_repository(project_root)
    with pytest.raises(Exception, match="secret-like"):
        propose_candidate(
            report,
            OnboardingAnswers(
                alias="fixture",
                warehouse_project="example-project",
                read_datasets=["source"],
                dbt_write_datasets=["dbt_dev"],
                target_datasets={"dev": "dbt_dev"},
                dbt_environment={"API_TOKEN": "not-allowed"},
            ),
        )
