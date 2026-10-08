from __future__ import annotations

import pytest

from dbt_agents.config import AppConfig, AuthConfig
from dbt_agents.errors import PolicyDenied
from dbt_agents.service import DbtAgentsService


def test_exact_plan_allows_one_guarded_edit(app_config: AppConfig) -> None:
    service = DbtAgentsService(app_config)
    read = service.repo_read("fixture", "dbt/models/model.sql")
    sha = read["data"]["sha256"]
    details = {"path": "dbt/models/model.sql", "expected_sha256": sha}
    plan = service.change_plan("fixture", "repo_apply_patch", details)
    # A second process/service instance can consume the persisted plan.
    consumer = DbtAgentsService(app_config)
    result = consumer.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        "select 2 as id\n",
        sha,
        approved=True,
        plan_id=plan["data"]["plan_id"],
    )
    assert result["ok"] is True

    reused = consumer.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        "select 3 as id\n",
        result["data"]["sha256"],
        approved=True,
        plan_id=plan["data"]["plan_id"],
    )
    assert reused["ok"] is False
    assert reused["error"] == "policy_denied"


def test_missing_approval_does_not_consume_plan(app_config: AppConfig) -> None:
    service = DbtAgentsService(app_config)
    read = service.repo_read("fixture", "dbt/models/model.sql")
    sha = read["data"]["sha256"]
    details = {"path": "dbt/models/model.sql", "expected_sha256": sha}
    plan = service.change_plan("fixture", "repo_apply_patch", details)
    denied = service.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        "select 2 as id\n",
        sha,
        approved=False,
        plan_id=plan["data"]["plan_id"],
    )
    assert denied["error"] == "approval_required"
    allowed = service.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        "select 2 as id\n",
        sha,
        approved=True,
        plan_id=plan["data"]["plan_id"],
    )
    assert allowed["ok"] is True


def test_plan_argument_mismatch_is_rejected(app_config: AppConfig) -> None:
    service = DbtAgentsService(app_config)
    plan = service.change_plan(
        "fixture",
        "dbt_execute",
        {"command": "run", "selector": "model", "target": "dev", "full_refresh": False},
    )
    result = service.dbt_execute(
        "fixture",
        "build",
        "model",
        target="dev",
        full_refresh=False,
        approved=True,
        plan_id=plan["data"]["plan_id"],
    )
    assert result["ok"] is False
    assert result["error"] == "policy_denied"


def test_dbt_test_always_requires_write_approval(app_config: AppConfig) -> None:
    service = DbtAgentsService(app_config)
    plan = service.change_plan("fixture", "dbt_test", {"selector": "model", "target": "dev"})
    result = service.dbt_test(
        "fixture",
        "model",
        target="dev",
        approved=False,
        plan_id=plan["data"]["plan_id"],
    )
    assert result["ok"] is False
    assert result["error"] == "approval_required"
    assert result["details"]["level"] == 2


def test_runtime_rejects_same_impersonated_identity(app_config: AppConfig, monkeypatch) -> None:
    project = app_config.projects["fixture"]
    project.warehouse.auth = AuthConfig(
        mode="impersonation",
        query_service_account_env="QUERY_ACCOUNT",
        dbt_service_account_env="DBT_ACCOUNT",
    )
    monkeypatch.setenv("QUERY_ACCOUNT", "same@example.iam.gserviceaccount.com")
    monkeypatch.setenv("DBT_ACCOUNT", "same@example.iam.gserviceaccount.com")
    with pytest.raises(PolicyDenied, match="different service accounts"):
        DbtAgentsService(app_config).project_get("fixture")
