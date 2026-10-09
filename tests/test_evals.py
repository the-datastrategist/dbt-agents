from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dbt_agents.config import ProjectConfig
from dbt_agents.errors import PolicyDenied
from dbt_agents.policy import SqlPolicy

ROOT = Path(__file__).resolve().parents[1]


def test_eval_catalog_has_unique_ids_and_required_categories() -> None:
    catalog = yaml.safe_load((ROOT / "evals/cases.yml").read_text(encoding="utf-8"))
    cases = catalog["cases"]
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert {case["category"] for case in cases} >= {
        "project-discovery",
        "onboarding",
        "warehouse-policy",
        "repository-safety",
        "approval",
        "dbt-boundary",
        "prompt-injection",
    }


def test_project_discovery_eval_calls_list_before_get() -> None:
    catalog = yaml.safe_load((ROOT / "evals/cases.yml").read_text(encoding="utf-8"))
    case = next(case for case in catalog["cases"] if case["id"] == "discover-configured-project")
    assert case["expected_tool_calls"] == [
        {"tool": "project_list"},
        {"tool": "project_get", "arguments": {"project": "keylo"}},
    ]


def test_onboarding_eval_requires_inspection_before_registration() -> None:
    catalog = yaml.safe_load((ROOT / "evals/cases.yml").read_text(encoding="utf-8"))
    case = next(case for case in catalog["cases"] if case["id"] == "onboard-second-project")
    assert case["expected_cli_calls"] == [
        {"command": "onboard inspect"},
        {"command": "onboard propose"},
        {"command": "onboard register"},
    ]


def test_sql_policy_eval_cases(project_config: ProjectConfig) -> None:
    policy = SqlPolicy(project_config)
    catalog = yaml.safe_load((ROOT / "evals/cases.yml").read_text(encoding="utf-8"))
    for case in catalog["cases"]:
        if "input_sql" not in case:
            continue
        if case["expected"] == "allowed":
            policy.validate_read_query(case["input_sql"])
        else:
            with pytest.raises(PolicyDenied, match=".+"):
                policy.validate_read_query(case["input_sql"])
