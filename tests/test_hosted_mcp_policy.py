import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def hosted_server():
    env = {
        "GCP_PROJECT_ID": "verdant-abacus-481415-d2",
        "BIGQUERY_BILLING_PROJECT_ID": "verdant-abacus-481415-d2",
        "BIGQUERY_ALLOWED_DATASETS": "keylo_dbt_marts",
        "AUTHORIZED_EMAILS": "gordon.silvera@gmail.com,giacomohasbani14@gmail.com",
        "SERVER_URL": "https://example.run.app",
        "MCP_ALLOWED_HOSTS": "dbt-agents-mcp-ajjw5yxfta-uc.a.run.app",
        "OAUTH_SIGNING_SECRET": "x" * 64,
        "FIREBASE_API_KEY": "test",
        "COST_BUCKET": "test-cost-bucket",
    }
    module_path = Path(__file__).parents[1] / "deploy" / "gcp" / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("hosted_dbt_agents_mcp", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with (
        patch.dict("os.environ", env, clear=False),
        patch("google.cloud.bigquery.Client"),
        patch("google.cloud.storage.Client"),
    ):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def brand_monitor_hosted_server():
    env = {
        "GCP_PROJECT_ID": "brand-monitor-platform-prod",
        "BIGQUERY_BILLING_PROJECT_ID": "brand-monitor-platform-prod",
        "FIREBASE_PROJECT_ID": "brand-monitor-platform-prod",
        "CONTROL_PROJECT_ID": "brand-monitor-platform-prod",
        "BIGQUERY_ALLOWED_PROJECT_DATASETS": (
            "brand-monitor-platform-dev.brand_monitor_dev_dbt,"
            "brand-monitor-platform-prod.brand_monitor_m0_dbt"
        ),
        "DATA_DICTIONARY_PROJECT_ID": "brand-monitor-platform-prod",
        "DATA_DICTIONARY_DATASET_ID": "brand_monitor_m0_dbt",
        "AUTHORIZED_EMAILS": "gordon.silvera@gmail.com,ashek.r.ahmed@gmail.com",
        "SERVER_URL": "https://brand-monitor-dbt-agents-mcp.example.run.app",
        "OAUTH_SIGNING_SECRET": "x" * 64,
        "FIREBASE_API_KEY": "test",
        "COST_BUCKET": "brand-monitor-test-cost-bucket",
    }
    module_path = Path(__file__).parents[1] / "deploy" / "gcp" / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location(
        "brand_monitor_hosted_dbt_agents_mcp", module_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with (
        patch.dict("os.environ", env, clear=False),
        patch("google.cloud.bigquery.Client"),
        patch("google.cloud.storage.Client"),
    ):
        spec.loader.exec_module(module)
    return module


def test_hosted_server_accepts_bounded_read_only_sql(hosted_server):
    sql = "SELECT * FROM `verdant-abacus-481415-d2.keylo_dbt_marts.example` LIMIT 10"
    assert hosted_server.validate_sql(sql) == {"verdant-abacus-481415-d2.keylo_dbt_marts"}


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM `verdant-abacus-481415-d2.keylo_dbt_marts.example` WHERE true",
        "SELECT * FROM `verdant-abacus-481415-d2.keylo_dbt_staging.example`",
        "SELECT * FROM `other-project.keylo_dbt_marts.example`",
        "SELECT * FROM example",
    ],
)
def test_hosted_server_rejects_unsafe_or_out_of_scope_sql(hosted_server, sql):
    with pytest.raises(ValueError):
        hosted_server.validate_sql(sql)


def test_hosted_server_limits_oauth_redirects(hosted_server):
    assert hosted_server._valid_redirect("https://chatgpt.com/connector/oauth/example")
    assert not hosted_server._valid_redirect("https://evil.example/connector/oauth/example")


def test_hosted_server_authorizes_only_internal_accounts(hosted_server):
    assert hosted_server.AUTHORIZED_EMAILS == {
        "gordon.silvera@gmail.com",
        "giacomohasbani14@gmail.com",
    }


def test_hosted_server_allows_public_and_cloud_run_backend_hosts(hosted_server):
    assert hosted_server.MCP_ALLOWED_HOSTS == {
        "example.run.app",
        "dbt-agents-mcp-ajjw5yxfta-uc.a.run.app",
    }


def test_firebase_hosting_proxies_to_the_internal_cloud_run_service():
    root = Path(__file__).parents[1]
    config = json.loads((root / "firebase.json").read_text())
    hosting = config["hosting"]

    assert hosting["site"] == "verdant-abacus-481415-d2"
    assert hosting["rewrites"] == [
        {
            "source": "**",
            "run": {
                "serviceId": "dbt-agents-mcp",
                "region": "us-central1",
            },
        }
    ]


def test_hosted_server_exposes_a_non_reserved_health_path(hosted_server):
    paths = {route.path for route in hosted_server.routes}
    assert "/health" in paths


def test_brand_monitor_server_allows_only_exact_project_dataset_pairs(
    brand_monitor_hosted_server,
):
    sql = """
    SELECT prod.metric_id, dev.metric_id
    FROM `brand-monitor-platform-prod.brand_monitor_m0_dbt.metrics` AS prod
    JOIN `brand-monitor-platform-dev.brand_monitor_dev_dbt.metrics` AS dev USING (metric_id)
    """
    assert brand_monitor_hosted_server.validate_sql(sql) == {
        "brand-monitor-platform-dev.brand_monitor_dev_dbt",
        "brand-monitor-platform-prod.brand_monitor_m0_dbt",
    }


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM `verdant-abacus-481415-d2.keylo_dbt_marts.example`",
        "SELECT * FROM `brand-monitor-platform-prod.brand_monitor_m0.example`",
        "SELECT * FROM `brand-monitor-platform-dev.brand_monitor_dev_raw.example`",
        "SELECT * FROM `brand_monitor_m0_dbt.example`",
    ],
)
def test_brand_monitor_server_rejects_keylo_operational_and_unqualified_data(
    brand_monitor_hosted_server, sql
):
    with pytest.raises(ValueError):
        brand_monitor_hosted_server.validate_sql(sql)


def test_brand_monitor_server_maps_tool_datasets_to_their_projects(
    brand_monitor_hosted_server,
):
    assert brand_monitor_hosted_server.DATASET_PROJECTS == {
        "brand_monitor_dev_dbt": "brand-monitor-platform-dev",
        "brand_monitor_m0_dbt": "brand-monitor-platform-prod",
    }
    assert (
        brand_monitor_hosted_server._require_dataset("brand_monitor_dev_dbt")
        == "brand-monitor-platform-dev"
    )
    with pytest.raises(ValueError):
        brand_monitor_hosted_server._require_dataset("keylo_dbt_marts")


def test_brand_monitor_oauth_is_scoped_to_brand_monitor_identity_project(
    brand_monitor_hosted_server,
):
    assert brand_monitor_hosted_server.FIREBASE_PROJECT_ID == "brand-monitor-platform-prod"
    assert brand_monitor_hosted_server.AUTHORIZED_EMAILS == {
        "gordon.silvera@gmail.com",
        "ashek.r.ahmed@gmail.com",
    }
