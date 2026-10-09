from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_ci_example_is_workload_identity_only_and_ci_scoped() -> None:
    config = yaml.safe_load((ROOT / "examples/keylo-ci.dbt-agents.yml").read_text())
    project = config["projects"]["keylo"]
    assert project["warehouse"]["auth"]["mode"] == "workload_identity"
    assert project["warehouse"]["dbt_write_datasets"] == ["keylo_dbt_ci"]
    assert project["dbt"]["target_datasets"] == {"ci": "keylo_dbt_ci"}
    assert project["warehouse"]["forbidden_write_datasets"] == [
        "vertex_dev",
        "keylo_dbt_staging",
        "keylo_dbt_intermediate",
        "keylo_dbt_marts",
        "keylo_dbt_audit",
    ]


def test_reusable_ci_workflow_uses_oidc_and_an_immutable_input_ref() -> None:
    workflow = (ROOT / ".github/workflows/keylo-dbt-validation.yml").read_text()
    assert "workflow_call:" in workflow
    assert "id-token: write" in workflow
    assert "DBT_SELECTOR: ${{ inputs.selector }}" in workflow
    assert '--selector "$DBT_SELECTOR"' in workflow
    assert "--selector '${{ inputs.selector }}'" not in workflow
    assert "google-github-actions/auth@v3" in workflow
    assert "dbt_agents_ref" in workflow
    assert "keylo_dbt_ci" in workflow
