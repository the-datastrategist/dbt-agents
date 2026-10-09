from __future__ import annotations

import pytest

from dbt_agents.adapters.dbt import DbtAdapter
from dbt_agents.config import ProjectConfig
from dbt_agents.errors import PolicyDenied
from dbt_agents.models import CommandResult


@pytest.mark.parametrize("selector", ["", "--target prod", "model\n--full-refresh"])
def test_rejects_selector_argument_injection(project_config: ProjectConfig, selector: str) -> None:
    with pytest.raises(PolicyDenied):
        DbtAdapter(project_config)._validate_selector(selector)


def test_requires_explicit_target_dataset_mapping(project_config: ProjectConfig) -> None:
    adapter = DbtAdapter(project_config)
    with pytest.raises(PolicyDenied, match="target_datasets"):
        adapter.target_dataset("missing")


def test_selector_resolution_uses_manifest_target_fields(
    project_config: ProjectConfig, monkeypatch
) -> None:
    adapter = DbtAdapter(project_config)
    manifest = adapter.project_dir / "target" / "manifest.json"
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(
        '{"nodes":{"model.fixture.model":{"database":"example-project",'
        '"schema":"dbt_dev","alias":"model"}}}',
        encoding="utf-8",
    )
    output = '{"unique_id":"model.fixture.model","name":"model","resource_type":"model"}\n'
    monkeypatch.setattr(
        adapter,
        "_execute",
        lambda *args, **kwargs: CommandResult(
            argv=["dbt", "list"],
            cwd=str(adapter.project_dir),
            exit_code=0,
            stdout=output,
            stderr="",
            duration_ms=1,
        ),
    )
    nodes = adapter.resolve_selector("model", "dev")
    assert nodes[0]["database"] == "example-project"
    assert nodes[0]["schema"] == "dbt_dev"


def test_compile_uses_minimal_environment_and_disables_introspection(
    project_config: ProjectConfig, monkeypatch
) -> None:
    adapter = DbtAdapter(project_config)
    captured = {}
    monkeypatch.setenv("UNRELATED_HOST_SECRET", "must-not-reach-dbt")

    def fake_run_command(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return CommandResult(
            argv=argv,
            cwd=str(adapter.project_dir),
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
        )

    monkeypatch.setattr("dbt_agents.adapters.dbt.run_command", fake_run_command)
    adapter.run("compile")

    assert "--no-introspect" in captured["argv"]
    assert captured["inherit_env"] is False
    assert "UNRELATED_HOST_SECRET" not in captured["env"]


def test_read_only_dbt_commands_use_query_credentials(
    project_config: ProjectConfig, monkeypatch
) -> None:
    project_config.warehouse.auth.mode = "service_account_file"
    monkeypatch.setenv("DBT_AGENTS_QUERY_CREDENTIALS", "/query.json")
    monkeypatch.setenv("DBT_AGENTS_DBT_CREDENTIALS", "/runner.json")
    adapter = DbtAdapter(project_config)

    assert adapter._auth_environment("compile") == {"GOOGLE_APPLICATION_CREDENTIALS": "/query.json"}
    assert adapter._auth_environment("run") == {"GOOGLE_APPLICATION_CREDENTIALS": "/runner.json"}
