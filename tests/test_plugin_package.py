from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins" / "dbt-agents"
BRAND_MONITOR_PLUGIN = ROOT / "plugins" / "dbt-agents-brand-monitor"


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_internal_plugin_wraps_the_registered_dbt_agents_app() -> None:
    app = _json(PLUGIN / ".app.json")
    assert app == {
        "apps": {
            "dbt-agents": {
                "id": "asdk_app_6ad2148876688191acba1d6ed6a425bb",
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
    assert interface["shortDescription"] == "Read-only analytics for dbt marts"

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
    assert "list_datasets" in skill
    assert "search_data_dictionary" in skill
    assert "estimate_query_cost" in skill
    assert "Do not request credentials or tokens" in skill


def test_repo_marketplace_exposes_and_enables_plugin() -> None:
    marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    entry = entries["dbt-agents"]

    assert marketplace["name"] == "thedatastrategist-internal"
    assert entry["name"] == "dbt-agents"
    assert entry["source"]["path"] == "./plugins/dbt-agents"
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }

    config = (ROOT / ".codex" / "config.toml").read_text()
    assert '[plugins."dbt-agents@thedatastrategist-internal"]' in config
    assert '[plugins."dbt-agents-brand-monitor@thedatastrategist-internal"]' in config
    assert "enabled = true" in config


def test_brand_monitor_plugin_wraps_the_isolated_registered_app() -> None:
    app = _json(BRAND_MONITOR_PLUGIN / ".app.json")
    assert app == {
        "apps": {
            "dbt-agents-brand-monitor": {
                "id": "asdk_app_6ad6d477a15481918444081adbb316b4",
                "required": True,
            }
        }
    }

    manifest = _json(BRAND_MONITOR_PLUGIN / ".codex-plugin" / "plugin.json")
    interface = manifest["interface"]
    assert manifest["name"] == "dbt-agents-brand-monitor"
    assert interface["displayName"] == "dbt Agents — Brand Monitor"
    assert "no access to operational datasets or Keylo data" in interface["longDescription"]

    for field in ("composerIcon", "logo"):
        asset = interface[field].removeprefix("./")
        assert (BRAND_MONITOR_PLUGIN / asset).is_file()

    skill = (BRAND_MONITOR_PLUGIN / "skills" / "get-started" / "SKILL.md").read_text()
    assert "brand-monitor-platform-dev.brand_monitor_dev_dbt" in skill
    assert "brand-monitor-platform-prod.brand_monitor_m0_dbt" in skill
    assert "Never request credentials" in skill

    marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    brand_entry = entries["dbt-agents-brand-monitor"]
    assert brand_entry["source"]["path"] == "./plugins/dbt-agents-brand-monitor"
    assert brand_entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
