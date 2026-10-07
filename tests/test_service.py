from __future__ import annotations

from dbt_agents.config import AppConfig
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
