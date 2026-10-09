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
        self.directory.chmod(0o700)
        plan_id = str(uuid4())
        record = {
            "plan_id": plan_id,
            "project": project,
            "action": action,
            "details": details,
            "expires_at": (datetime.now(UTC) + self.ttl).isoformat(),
            "approved_at": None,
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

    def approve(self, plan_id: str, project: str, action: str, details: dict[str, Any]) -> None:
        """Persist an out-of-band approval; this method is intentionally not an MCP tool."""
        self._validate_id(plan_id)
        source = self.directory / f"{plan_id}.json"
        claimed = self.directory / f".{plan_id}.{uuid4()}.approving"
        try:
            os.replace(source, claimed)
        except FileNotFoundError as exc:
            raise PolicyDenied(
                "valid unused change plan is required", {"plan_id": plan_id}
            ) from exc
        temporary: str | None = None
        try:
            plan = self._load(claimed, plan_id)
            self._validate_plan(plan, plan_id, project, action, details, require_approved=False)
            plan["approved_at"] = datetime.now(UTC).isoformat()
            descriptor, temporary = tempfile.mkstemp(prefix=".approved-", dir=self.directory)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(plan, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, source)
            temporary = None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
            if claimed.exists():
                if not source.exists():
                    os.replace(claimed, source)
                else:
                    claimed.unlink()

    def consume(self, plan_id: str, project: str, action: str, details: dict[str, Any]) -> None:
        self._validate_id(plan_id)
        source = self.directory / f"{plan_id}.json"
        consumed = self.directory / f".{plan_id}.{uuid4()}.consumed"
        try:
            os.replace(source, consumed)
        except FileNotFoundError as exc:
            raise PolicyDenied(
                "valid unused change plan is required", {"plan_id": plan_id}
            ) from exc
        try:
            plan = self._load(consumed, plan_id)
            self._validate_plan(plan, plan_id, project, action, details, require_approved=True)
        finally:
            consumed.unlink(missing_ok=True)

    @staticmethod
    def _validate_id(plan_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f-]{36}", plan_id):
            raise PolicyDenied("invalid change plan ID")

    @staticmethod
    def _load(path: Path, plan_id: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise PolicyDenied("change plan is invalid", {"plan_id": plan_id}) from exc
        if not isinstance(value, dict):
            raise PolicyDenied("change plan is invalid", {"plan_id": plan_id})
        return value

    @staticmethod
    def _validate_plan(
        plan: dict[str, Any],
        plan_id: str,
        project: str,
        action: str,
        details: dict[str, Any],
        *,
        require_approved: bool,
    ) -> None:
        try:
            expires_at = datetime.fromisoformat(plan["expires_at"])
        except (TypeError, ValueError, KeyError) as exc:
            raise PolicyDenied("change plan is invalid", {"plan_id": plan_id}) from exc
        if expires_at < datetime.now(UTC):
            raise PolicyDenied("change plan expired", {"plan_id": plan_id})
        if (plan.get("project"), plan.get("action"), plan.get("details")) != (
            project,
            action,
            details,
        ):
            raise PolicyDenied("operation does not match approved plan", {"plan_id": plan_id})
        if require_approved and not plan.get("approved_at"):
            raise PolicyDenied(
                "change plan requires out-of-band approval",
                {"plan_id": plan_id, "approval": "run the local approve-plan command"},
            )
