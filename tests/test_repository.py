from __future__ import annotations

import pytest

from dbt_agents.adapters.repository import RepositoryAdapter
from dbt_agents.config import ProjectConfig
from dbt_agents.errors import StaleWorkspace


def test_read_and_hash_guarded_write(project_config: ProjectConfig) -> None:
    adapter = RepositoryAdapter(project_config)
    current = adapter.read("dbt/models/model.sql")
    result = adapter.write(
        "dbt/models/model.sql",
        "select 2 as id\n",
        current["sha256"],
    )
    assert result["created"] is False
    assert adapter.read("dbt/models/model.sql")["content"] == "select 2 as id"

    with pytest.raises(StaleWorkspace):
        adapter.write("dbt/models/model.sql", "select 3\n", current["sha256"])


def test_search_excludes_generated_and_secret_paths(project_config: ProjectConfig) -> None:
    adapter = RepositoryAdapter(project_config)
    (project_config.repo_root / "dbt" / ".env").write_text("needle", encoding="utf-8")
    target = project_config.repo_root / "dbt" / "target"
    target.mkdir()
    (target / "compiled.sql").write_text("needle", encoding="utf-8")
    result = adapter.search("needle")
    assert result["matches"] == []


def test_git_status_does_not_mutate(project_config: ProjectConfig) -> None:
    result = RepositoryAdapter(project_config).status(include_diff=True)
    assert "branch" in result
    assert result["status"]
