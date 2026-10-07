from __future__ import annotations

from pathlib import Path

import pytest

from dbt_agents.config import ProjectConfig, WarehouseConfig, load_config
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
