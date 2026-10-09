from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ApprovalLevel(IntEnum):
    INSPECT = 0
    BOUNDED_COMPUTE = 1
    LOCAL_CHANGE = 2
    REMOTE_OR_PRODUCTION = 3


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny", "approval_required"]
    level: ApprovalLevel
    rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project: str
    operation: str
    policy: PolicyDecision
    duration_ms: int = 0
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


class ProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    warehouse_provider: str
    warehouse_project: str
    dbt_default_target: str


class ProjectListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    projects: list[ProjectSummary]


class ConfigurationOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project: None = None
    operation: str
    policy: PolicyDecision
    duration_ms: int = 0
    data: ProjectListData
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
