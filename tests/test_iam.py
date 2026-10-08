from __future__ import annotations

import pytest

from dbt_agents.config import ProjectConfig
from dbt_agents.errors import PolicyDenied
from dbt_agents.iam import BigQueryIam, _roles_by_member


def test_iam_plan_requires_strong_two_identity_mode(project_config: ProjectConfig) -> None:
    with pytest.raises(PolicyDenied, match="identities"):
        BigQueryIam(project_config).plan()


def test_iam_plan_separates_query_and_runner(project_config: ProjectConfig, monkeypatch) -> None:
    config = project_config.model_copy(deep=True)
    config.warehouse.auth.mode = "impersonation"
    config.warehouse.auth.query_service_account_env = "QUERY_SA"
    config.warehouse.auth.dbt_service_account_env = "DBT_SA"
    monkeypatch.setenv("QUERY_SA", "query@example-project.iam.gserviceaccount.com")
    monkeypatch.setenv("DBT_SA", "runner@example-project.iam.gserviceaccount.com")
    plan = BigQueryIam(config).plan()
    source = [
        item for item in plan["bindings"] if "dataset:example-project.source" == item["scope"]
    ]
    assert {item["role"] for item in source} == {"READER"}
    target = [
        item for item in plan["bindings"] if "dataset:example-project.dbt_dev" == item["scope"]
    ]
    assert {item["role"] for item in target} == {"READER", "WRITER"}


def test_roles_by_member() -> None:
    result = _roles_by_member(
        {"bindings": [{"role": "roles/bigquery.jobUser", "members": ["serviceAccount:a"]}]}
    )
    assert result["serviceAccount:a"] == {"roles/bigquery.jobUser"}


def test_iam_apply_requires_explicit_approval(project_config: ProjectConfig) -> None:
    with pytest.raises(PolicyDenied, match="approve"):
        BigQueryIam(project_config).apply(
            query_identity="query@example-project.iam.gserviceaccount.com",
            dbt_identity="runner@example-project.iam.gserviceaccount.com",
            approved=False,
        )
