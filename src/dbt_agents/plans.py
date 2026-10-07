from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import PolicyDenied


class PlanStore:
    """Atomic, one-use approval plans shared by CLI and MCP processes."""

    def __init__(self, state_dir: Path, ttl: timedelta):
        self.directory = state_dir / "plans"
        self.ttl = ttl

    def create(self, project: str, action: str, details: dict[str, Any]) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        plan_id = str(uuid4())
        record = {
            "plan_id": plan_id,
            "project": project,
            "action": action,
            "details": details,
            "expires_at": (datetime.now(UTC) + self.ttl).isoformat(),
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".plan-", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.directory / f"{plan_id}.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return record

    def consume(self, plan_id: str, project: str, action: str, details: dict[str, Any]) -> None:
        if not re.fullmatch(r"[0-9a-f-]{36}", plan_id):
            raise PolicyDenied("invalid change plan ID")
        source = self.directory / f"{plan_id}.json"
        consumed = self.directory / f".{plan_id}.{uuid4()}.consumed"
        try:
            os.replace(source, consumed)
        except FileNotFoundError as exc:
            raise PolicyDenied(
                "valid unused change plan is required", {"plan_id": plan_id}
            ) from exc
        try:
            try:
                plan = json.loads(consumed.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(plan["expires_at"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise PolicyDenied("change plan is invalid", {"plan_id": plan_id}) from exc
            if expires_at < datetime.now(UTC):
                raise PolicyDenied("change plan expired", {"plan_id": plan_id})
            if (plan.get("project"), plan.get("action"), plan.get("details")) != (
                project,
                action,
                details,
            ):
                raise PolicyDenied("operation does not match approved plan", {"plan_id": plan_id})
        finally:
            consumed.unlink(missing_ok=True)
