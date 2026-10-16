from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins" / "dbt-agents"


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_internal_plugin_wraps_the_registered_dbt_agents_app() -> None:
    app = _json(PLUGIN / ".app.json")
    assert app == {
        "apps": {
            "dbt-agents": {
                "id": "asdk_app_6ac8ba5335cc81919a354d935544cacb",
                "required": True,
            }
        }
    }


def test_plugin_is_branded_as_dbt_agents() -> None:
    manifest = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    interface = manifest["interface"]

    assert manifest["name"] == "dbt-agents"
    assert manifest["apps"] == "./.app.json"
    assert manifest["skills"] == "./skills/"
    assert interface["displayName"] == "dbt Agents"
    assert interface["category"] == "Developer Tools"
    assert interface["shortDescription"] == "Safe AI workflows for dbt"

    listing_text = " ".join(
        [
            manifest["description"],
            interface["displayName"],
            interface["shortDescription"],
            interface["longDescription"],
        ]
    ).lower()
    assert "bigquery to chatgpt connection" not in listing_text

    for field in ("composerIcon", "logo"):
        asset = interface[field].removeprefix("./")
        assert (PLUGIN / asset).is_file()


def test_get_started_skill_is_packaged() -> None:
    skill = (PLUGIN / "skills" / "get-started" / "SKILL.md").read_text()
    assert "name: dbt-agents-get-started" in skill
    assert "project_list" in skill
    assert "project_readiness" in skill
    assert "Do not request credentials or tokens" in skill


def test_repo_marketplace_exposes_and_enables_plugin() -> None:
    marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    entry = marketplace["plugins"][0]

    assert marketplace["name"] == "thedatastrategist-internal"
    assert entry["name"] == "dbt-agents"
    assert entry["source"]["path"] == "./plugins/dbt-agents"
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }

    config = (ROOT / ".codex" / "config.toml").read_text()
    assert '[plugins."dbt-agents@thedatastrategist-internal"]' in config
    assert "enabled = true" in config
