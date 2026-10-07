from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DbtAgentsError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


class ConfigurationError(DbtAgentsError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("configuration_error", message, details)


class PolicyDenied(DbtAgentsError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("policy_denied", message, details)


class ApprovalRequired(DbtAgentsError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("approval_required", message, details)


class ExecutionFailed(DbtAgentsError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("execution_failed", message, details)


class StaleWorkspace(DbtAgentsError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("stale_workspace", message, details)
