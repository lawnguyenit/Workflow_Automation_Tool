from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ResultState(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILURE = "FAILURE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Retryability(StrEnum):
    RETRYABLE = "RETRYABLE"
    NOT_RETRYABLE = "NOT_RETRYABLE"
    UNKNOWN = "UNKNOWN"


class RunMode(StrEnum):
    REAL = "REAL"
    MOCK = "MOCK"
    DRY_RUN = "DRY_RUN"
    REPLAY = "REPLAY"


class ModuleError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    category: str
    message: str
    retryability: Retryability = Retryability.UNKNOWN
    diagnostic_ref: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
    attempt: int = Field(default=1, ge=1)
    mode: RunMode = RunMode.MOCK
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ModuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: str
    contract_version: str = "0.1"
    status: ResultState
    business_result: dict[str, Any] = Field(default_factory=dict)
    errors: list[ModuleError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)
    context: ExecutionContext
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def complete(self) -> "ModuleResult":
        self.finished_at = datetime.now(UTC)
        return self
