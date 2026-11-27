from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)
from workflow_automation.infrastructure.postgres_source_poll_checkpoint import (
    PostgresSourcePollCheckpoint,
    SourcePollCheckpointError,
)

_KEY_HASH = re.compile(r"^[a-f0-9]{64}$")
_OWNER = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


class SourcePollCheckpoint:
    """Durable per-source lease and fenced timestamp checkpoint.

    It stores only a hash of the source key. A lease owner can reacquire its
    lease after a lost HTTP response; a different owner receives BUSY until
    release or expiry. Only the current unexpired lease may commit a cursor.
    """

    module_id = "state.source_poll_checkpoint"

    def __init__(self, *, store: PostgresSourcePollCheckpoint | Any | None = None,
                 lease_seconds: int = 900) -> None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 60 <= lease_seconds <= 7200:
            raise ValueError("lease_seconds must be an integer from 60 through 7200")
        self._store = store or PostgresSourcePollCheckpoint()
        self._lease_seconds = lease_seconds

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        issue = self._validate(payload)
        if issue:
            return self._result(context, started, ResultState.FAILURE,
                                {"state": "INVALID_INPUT"},
                                error=("INVALID_SOURCE_POLL_CHECKPOINT_INPUT", issue,
                                       Retryability.NOT_RETRYABLE))

        if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
            state = "PREVIEWED"
            return self._result(context, started, ResultState.SUCCESS,
                                {"state": state, "cursor_at": None, "lease_token": None,
                                 "lease_expires_at": None})

        source_key_hash = hashlib.sha256(payload["checkpoint_key"].encode("utf-8")).hexdigest()
        operation = payload["operation"]
        try:
            if operation == "acquire":
                outcome = self._store.acquire(
                    key_hash=source_key_hash,
                    owner_id=payload["lease_owner_id"],
                    lease_seconds=self._lease_seconds,
                )
            elif operation == "commit":
                outcome = self._store.commit(
                    key_hash=source_key_hash,
                    owner_id=payload["lease_owner_id"],
                    lease_token=UUID(payload["lease_token"]),
                    cursor_at=self._parse_cursor(payload["cursor_at"]),
                )
            elif operation == "renew":
                outcome = self._store.renew(
                    key_hash=source_key_hash,
                    owner_id=payload["lease_owner_id"],
                    lease_token=UUID(payload["lease_token"]),
                    lease_seconds=self._lease_seconds,
                )
            else:
                outcome = self._store.release(
                    key_hash=source_key_hash,
                    owner_id=payload["lease_owner_id"],
                    lease_token=UUID(payload["lease_token"]),
                )
        except SourcePollCheckpointError as exc:
            return self._result(
                context, started, ResultState.REVIEW_REQUIRED,
                {"state": exc.code},
                error=(exc.code, "Durable source polling state is unavailable; no checkpoint was advanced",
                       Retryability.UNKNOWN),
            )
        except (ValueError, TypeError):
            return self._result(context, started, ResultState.FAILURE,
                                {"state": "INVALID_INPUT"},
                                error=("INVALID_SOURCE_POLL_CHECKPOINT_INPUT",
                                       "lease_token or cursor_at is malformed",
                                       Retryability.NOT_RETRYABLE))

        state = outcome.get("state", "UNKNOWN")
        status = ResultState.SUCCESS if state in {"ACQUIRED", "COMMITTED", "RELEASED", "RENEWED", "BUSY"} else ResultState.REVIEW_REQUIRED
        error = None if status == ResultState.SUCCESS else (
            state, "Lease ownership is no longer current; do not advance the source cursor",
            Retryability.NOT_RETRYABLE,
        )
        safe_result = {"state": state}
        for field in ("cursor_at", "lease_token", "lease_expires_at"):
            if field in outcome:
                safe_result[field] = outcome[field]
        return self._result(context, started, status, safe_result, error=error)

    @staticmethod
    def _validate(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return "payload must be an object"
        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in {"acquire", "commit", "release", "renew"}:
            return "operation must be acquire, commit, release, or renew"
        required = {"operation", "checkpoint_key", "lease_owner_id"}
        if operation == "acquire":
            if set(payload) != required:
                return "acquire payload has missing or unexpected fields"
        else:
            required |= {"lease_token"}
            if operation == "commit":
                required |= {"cursor_at"}
            if set(payload) != required:
                return f"{operation} payload has missing or unexpected fields"
        key = payload.get("checkpoint_key")
        if not isinstance(key, str) or not key.strip() or len(key) > 500 or any(ord(c) < 32 for c in key):
            return "checkpoint_key must be non-empty text up to 500 characters"
        owner = payload.get("lease_owner_id")
        if not isinstance(owner, str) or not _OWNER.fullmatch(owner):
            return "lease_owner_id must be 1-100 safe characters"
        if operation != "acquire":
            try:
                UUID(payload.get("lease_token", ""))
            except (ValueError, TypeError, AttributeError):
                return "lease_token must be a UUID"
        if operation == "commit":
            try:
                SourcePollCheckpoint._parse_cursor(payload.get("cursor_at"))
            except (ValueError, TypeError):
                return "cursor_at must be an RFC3339 UTC timestamp"
        return None

    @staticmethod
    def _parse_cursor(value: Any) -> datetime:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("invalid cursor")
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("cursor must be UTC")
        return parsed.astimezone(UTC)

    def _result(self, context: ExecutionContext, started: datetime, status: ResultState,
                business_result: dict[str, Any], *,
                error: tuple[str, str, Retryability] | None = None) -> ModuleResult:
        errors = []
        if error:
            errors.append(ModuleError(code=error[0], category="SOURCE_STATE", message=error[1],
                                      retryability=error[2]))
        return ModuleResult(module_id=self.module_id, status=status, business_result=business_result,
                            errors=errors, metrics={"state": business_result.get("state", "UNKNOWN")},
                            context=context, started_at=started).complete()
