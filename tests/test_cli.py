from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dbt_agents.cli import app
from dbt_agents.config import AppConfig
from dbt_agents.service import DbtAgentsService


def test_projects_command_lists_aliases(app_config: AppConfig, monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "dbt-agents.yml"
    config.write_text("placeholder\n", encoding="utf-8")
    monkeypatch.setattr(
        "dbt_agents.cli._service",
        lambda _: DbtAgentsService(app_config),
    )

    result = CliRunner().invoke(app, ["projects", "--config", str(config)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["project"] is None
    assert payload["data"]["projects"] == [
        {
            "id": "fixture",
            "warehouse_provider": "bigquery",
            "warehouse_project": "example-project",
            "dbt_default_target": "dev",
        }
    ]
