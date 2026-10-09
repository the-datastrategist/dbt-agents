from __future__ import annotations

import asyncio

from dbt_agents.config import AppConfig
from dbt_agents.server import create_server


def test_mcp_tools_have_explicit_safety_annotations(app_config: AppConfig, monkeypatch) -> None:
    monkeypatch.setattr("dbt_agents.server.load_config", lambda _: app_config)
    server = create_server("unused.yml")
    tools = asyncio.run(server.list_tools())
    annotations = {tool.name: tool.annotations for tool in tools}

    assert set(annotations) == {
        "project_list",
        "project_get",
        "project_readiness",
        "repo_search",
        "repo_read",
        "repo_status",
        "dbt_inspect",
        "dbt_compile",
        "warehouse_describe",
        "warehouse_query",
        "change_plan",
        "repo_apply_patch",
        "dbt_test",
        "dbt_execute",
        "git_publish",
    }
    assert annotations["project_list"].readOnlyHint is True
    assert annotations["warehouse_query"].readOnlyHint is True
    assert annotations["repo_apply_patch"].readOnlyHint is False
    assert annotations["repo_apply_patch"].destructiveHint is False
    assert annotations["dbt_execute"].destructiveHint is True
    assert annotations["git_publish"].destructiveHint is True


def test_mcp_v1_required_inputs_are_frozen(app_config: AppConfig, monkeypatch) -> None:
    monkeypatch.setattr("dbt_agents.server.load_config", lambda _: app_config)
    tools = asyncio.run(create_server("unused.yml").list_tools())
    required = {tool.name: set(tool.inputSchema.get("required", [])) for tool in tools}
    assert required == {
        "project_list": set(),
        "project_get": {"project"},
        "project_readiness": {"project"},
        "repo_search": {"project", "query"},
        "repo_read": {"project", "path"},
        "repo_status": {"project"},
        "dbt_inspect": {"project"},
        "dbt_compile": {"project"},
        "warehouse_describe": {"project", "relation"},
        "warehouse_query": {"project", "sql"},
        "change_plan": {"project", "action", "details"},
        "repo_apply_patch": {
            "project",
            "path",
            "content",
            "expected_sha256",
            "plan_id",
        },
        "dbt_test": {"project", "selector", "plan_id"},
        "dbt_execute": {"project", "command", "selector", "plan_id"},
        "git_publish": {"project", "action", "plan_id"},
    }
