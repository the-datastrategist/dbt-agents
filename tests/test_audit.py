from __future__ import annotations

import stat

from dbt_agents.audit import AuditLogger


def test_audit_log_and_directory_are_private(tmp_path) -> None:
    directory = tmp_path / "audit"
    logger = AuditLogger(directory)

    logger.write({"operation": "test"})

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(logger.path.stat().st_mode) == 0o600
