from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dbt_agents.config import (
    AppConfig,
    AuthConfig,
    DbtConfig,
    ProjectConfig,
    WarehouseConfig,
    WarehouseLimits,
)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    dbt_dir = root / "dbt"
    (dbt_dir / "models").mkdir(parents=True)
    (dbt_dir / "profiles").mkdir()
    (dbt_dir / "dbt_project.yml").write_text(
        "name: fixture\nversion: 1.0.0\nconfig-version: 2\nprofile: fixture\n",
        encoding="utf-8",
    )
    (dbt_dir / "models" / "model.sql").write_text("select 1 as id\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=dbt_dir, check=True)
    return root


@pytest.fixture
def project_config(project_root: Path) -> ProjectConfig:
    return ProjectConfig(
        repo_root=project_root,
        git_root=Path("dbt"),
        dbt_project_dir=Path("dbt"),
        writable_paths=["dbt/models/**", "dbt/tests/**"],
        protected_paths=["**/.env*", "**/*credential*", "**/*.key"],
        dbt=DbtConfig(
            profiles_dir="profiles",
            default_target="dev",
            production_targets=["prod"],
            target_datasets={"dev": "dbt_dev", "prod": "dbt_prod"},
        ),
        warehouse=WarehouseConfig(
            provider="bigquery",
            project="example-project",
            location="US",
            default_dataset="source",
            read_datasets=["source", "dbt_dev", "dbt_prod"],
            dbt_write_datasets=["dbt_dev", "dbt_prod"],
            forbidden_write_datasets=["source"],
            allow_raw_rows=True,
            auth=AuthConfig(mode="adc"),
        ),
        limits=WarehouseLimits(
            query_max_bytes_billed=1000,
            query_max_rows=2,
            query_max_result_bytes=1024,
            file_max_bytes=20_000,
        ),
    )


@pytest.fixture
def app_config(project_config: ProjectConfig) -> AppConfig:
    return AppConfig(version=1, projects={"fixture": project_config})
