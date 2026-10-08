from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_and_cli_names_are_frozen() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "dbt-agents"
    assert pyproject["project"]["scripts"] == {
        "dbt-agents": "dbt_agents.cli:app",
        "dbt-agents-mcp": "dbt_agents.server:main",
    }
    assert pyproject["project"]["requires-python"] == ">=3.11,<3.14"


def test_json_schema_matches_frozen_defaults() -> None:
    schema = json.loads((ROOT / "schemas/config.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["version"]["const"] == 1
    warehouse = schema["$defs"]["warehouse"]["properties"]
    limits = schema["$defs"]["limits"]["properties"]
    auth = schema["$defs"]["auth"]["properties"]

    assert warehouse["provider"]["const"] == "bigquery"
    assert warehouse["allow_raw_rows"]["default"] is False
    assert limits["query_max_bytes_billed"]["default"] == 1_000_000_000
    assert limits["query_timeout_seconds"]["default"] == 60
    assert limits["query_max_rows"]["default"] == 100
    assert limits["query_max_result_bytes"]["default"] == 2_000_000
    assert limits["query_max_cell_chars"]["default"] == 10_000
    assert set(auth["mode"]["enum"]) == {
        "adc",
        "impersonation",
        "service_account_file",
        "workload_identity",
    }
