from __future__ import annotations

import pytest

from dbt_agents.adapters.dbt import DbtAdapter
from dbt_agents.config import ProjectConfig
from dbt_agents.errors import PolicyDenied


@pytest.mark.parametrize("selector", ["", "--target prod", "model\n--full-refresh"])
def test_rejects_selector_argument_injection(project_config: ProjectConfig, selector: str) -> None:
    with pytest.raises(PolicyDenied):
        DbtAdapter(project_config)._validate_selector(selector)


def test_requires_explicit_target_dataset_mapping(project_config: ProjectConfig) -> None:
    adapter = DbtAdapter(project_config)
    with pytest.raises(PolicyDenied, match="target_datasets"):
        adapter.target_dataset("missing")
