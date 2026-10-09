from __future__ import annotations

import hashlib

import pytest

from dbt_agents.config import AppConfig, AuthConfig
from dbt_agents.errors import PolicyDenied
from dbt_agents.service import DbtAgentsService


def test_project_list_is_sorted_sanitized_and_configuration_scoped(app_config: AppConfig) -> None:
    second = app_config.projects["fixture"].model_copy(deep=True)
    second.warehouse.project = "another-project"
    second.dbt.default_target = "ci"
    config = AppConfig(
        version=1,
        projects={"zeta": second, "fixture": app_config.projects["fixture"]},
    )
    service = DbtAgentsService(config)

    result = service.project_list()

    assert result["ok"] is True
    assert result["project"] is None
    assert result["operation"] == "project_list"
    assert result["policy"]["level"] == 0
    assert result["policy"]["rule_ids"] == ["PROJECT-LIST"]
    assert result["data"] == {
        "count": 2,
        "projects": [
            {
                "id": "fixture",
                "warehouse_provider": "bigquery",
                "warehouse_project": "example-project",
                "dbt_default_target": "dev",
            },
            {
                "id": "zeta",
                "warehouse_provider": "bigquery",
                "warehouse_project": "another-project",
                "dbt_default_target": "ci",
            },
        ],
    }
    assert service._runtimes == {}


def test_unknown_project_recommends_discovery(app_config: AppConfig) -> None:
    with pytest.raises(PolicyDenied, match="call project_list") as error:
        DbtAgentsService(app_config).project_get("missing")
    assert error.value.details == {
        "project": "missing",
        "discovery_tool": "project_list",
    }


def test_project_readiness_reports_static_contract(app_config: AppConfig) -> None:
    result = DbtAgentsService(app_config).project_readiness("fixture")

    assert result["ok"] is True
    assert result["operation"] == "project_readiness"
    assert result["policy"]["rule_ids"] == ["PROJECT-READINESS"]
    assert result["data"]["repository"]["root"]


def test_exact_plan_allows_one_guarded_edit(app_config: AppConfig) -> None:
    service = DbtAgentsService(app_config)
    read = service.repo_read("fixture", "dbt/models/model.sql")
    sha = read["data"]["sha256"]
    content = "select 2 as id\n"
    details = {
        "path": "dbt/models/model.sql",
        "expected_sha256": sha,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    plan = service.change_plan("fixture", "repo_apply_patch", details)
    service.approve_plan(plan["data"]["plan_id"], "fixture", "repo_apply_patch", details)
    # A second process/service instance can consume the persisted plan.
    consumer = DbtAgentsService(app_config)
    result = consumer.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        content,
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
    content = "select 2 as id\n"
    details = {
        "path": "dbt/models/model.sql",
        "expected_sha256": sha,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    plan = service.change_plan("fixture", "repo_apply_patch", details)
    denied = service.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        content,
        sha,
        approved=False,
        plan_id=plan["data"]["plan_id"],
    )
    assert denied["error"] == "approval_required"
    service.approve_plan(plan["data"]["plan_id"], "fixture", "repo_apply_patch", details)
    allowed = service.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        content,
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
    service.approve_plan(
        plan["data"]["plan_id"],
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


def test_mcp_boolean_cannot_self_approve_plan(app_config: AppConfig) -> None:
    service = DbtAgentsService(app_config)
    read = service.repo_read("fixture", "dbt/models/model.sql")
    sha = read["data"]["sha256"]
    content = "select 2 as id\n"
    details = {
        "path": "dbt/models/model.sql",
        "expected_sha256": sha,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    plan = service.change_plan("fixture", "repo_apply_patch", details)

    result = service.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        content,
        sha,
        approved=True,
        plan_id=plan["data"]["plan_id"],
    )

    assert result["ok"] is False
    assert result["error"] == "policy_denied"
    assert "out-of-band" in result["message"]


def test_patch_plan_is_bound_to_content(app_config: AppConfig) -> None:
    service = DbtAgentsService(app_config)
    read = service.repo_read("fixture", "dbt/models/model.sql")
    sha = read["data"]["sha256"]
    approved_content = "select 2 as id\n"
    details = {
        "path": "dbt/models/model.sql",
        "expected_sha256": sha,
        "content_sha256": hashlib.sha256(approved_content.encode()).hexdigest(),
    }
    plan = service.change_plan("fixture", "repo_apply_patch", details)
    service.approve_plan(plan["data"]["plan_id"], "fixture", "repo_apply_patch", details)

    result = service.repo_apply_patch(
        "fixture",
        "dbt/models/model.sql",
        "select secret from source\n",
        sha,
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
