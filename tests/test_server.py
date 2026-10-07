from __future__ import annotations

import asyncio

from dbt_agents.config import AppConfig
from dbt_agents.server import create_server


def test_mcp_tools_have_explicit_safety_annotations(app_config: AppConfig, monkeypatch) -> None:
    monkeypatch.setattr("dbt_agents.server.load_config", lambda _: app_config)
    server = create_server("unused.yml")
    tools = asyncio.run(server.list_tools())
    annotations = {tool.name: tool.annotations for tool in tools}

    assert len(tools) == 13
    assert annotations["warehouse_query"].readOnlyHint is True
    assert annotations["repo_apply_patch"].readOnlyHint is False
    assert annotations["repo_apply_patch"].destructiveHint is False
    assert annotations["dbt_execute"].destructiveHint is True
    assert annotations["git_publish"].destructiveHint is True
