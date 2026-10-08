from __future__ import annotations

from pathlib import Path

import pytest

from dbt_agents.config import (
    AuthConfig,
    ProjectConfig,
    WarehouseConfig,
    WarehouseLimits,
    load_config,
)
from dbt_agents.errors import ConfigurationError


def test_rejects_overlapping_forbidden_write_dataset() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        WarehouseConfig(
            project="p",
            read_datasets=["source"],
            dbt_write_datasets=["source"],
            forbidden_write_datasets=["source"],
        )


def test_rejects_unknown_configuration_key(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text("version: 1\nunknown: true\nprojects: {}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(config)


def test_resolved_paths_stay_inside_root(project_config: ProjectConfig) -> None:
    assert project_config.resolved_dbt_project_dir == project_config.repo_root / "dbt"
    assert project_config.resolved_git_root == project_config.repo_root / "dbt"


def test_v1_safety_defaults_are_frozen() -> None:
    limits = WarehouseLimits()
    assert limits.query_max_bytes_billed == 1_000_000_000
    assert limits.query_timeout_seconds == 60
    assert limits.query_max_rows == 100
    assert limits.query_max_result_bytes == 2_000_000
    assert limits.query_max_cell_chars == 10_000

    warehouse = WarehouseConfig(
        project="p",
        read_datasets=["source", "target"],
        dbt_write_datasets=["target"],
    )
    assert warehouse.provider == "bigquery"
    assert warehouse.allow_raw_rows is False
    assert warehouse.auth.mode == "adc"


@pytest.mark.parametrize(
    "mode", ["adc", "impersonation", "service_account_file", "workload_identity"]
)
def test_v1_authentication_modes_are_supported(mode: str) -> None:
    assert AuthConfig(mode=mode).mode == mode


def test_uncontracted_authentication_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        AuthConfig(mode="api_key")


def test_query_and_dbt_credential_environment_names_must_differ() -> None:
    with pytest.raises(ValueError, match="must differ"):
        AuthConfig(
            mode="service_account_file",
            query_credentials_file_env="SAME_FILE",
            dbt_credentials_file_env="SAME_FILE",
        )


def test_query_and_dbt_impersonation_environment_names_must_differ() -> None:
    with pytest.raises(ValueError, match="must differ"):
        AuthConfig(
            mode="impersonation",
            query_service_account_env="SAME_ACCOUNT",
            dbt_service_account_env="SAME_ACCOUNT",
        )
