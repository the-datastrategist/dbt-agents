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
        "warehouse-policy",
        "repository-safety",
        "approval",
        "dbt-boundary",
        "prompt-injection",
    }


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
