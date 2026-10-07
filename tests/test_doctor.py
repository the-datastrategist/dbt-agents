from __future__ import annotations

from dbt_agents.config import ProjectConfig
from dbt_agents.doctor import Doctor


def test_doctor_reports_required_failures_without_raising(
    project_config: ProjectConfig, monkeypatch
) -> None:
    doctor = Doctor(project_config)
    monkeypatch.setattr(doctor, "_dbt", lambda: (_ for _ in ()).throw(RuntimeError("missing")))
    result = doctor.run(live=False)
    assert result["ok"] is False
    assert "dbt" in result["failed_required_checks"]


def test_doctor_offline_skips_bigquery(project_config: ProjectConfig) -> None:
    result = Doctor(project_config).run(live=False)
    assert all(check["name"] != "bigquery" for check in result["checks"])
