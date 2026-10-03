from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)
from workflow_automation.infrastructure.postgres_lead_followup_state import (
    FollowupEventConflict,
    FollowupStateError,
    FollowupStateNotConfigured,
    PostgresLeadFollowupState,
)

_EVENT_TYPES = {
    "FOLLOWUP_ENROLLED",
    "REPLIED",
    "BOOKED",
    "OPTED_OUT",
    "MANUAL_PAUSE",
    "MANUAL_RESUME",
}
_SOURCE_FIELDS = ("source_system", "source_account", "source_event_id")
_SOURCE_LIMITS = {"source_system": 100, "source_account": 200, "source_event_id": 250}


class LeadFollowupGate:
    """Durable fail-closed eligibility gate for future lead follow-up sends.

    This module decides eligibility only. Callers must check immediately before
    each send; it does not send, schedule, or cancel an already-started request.
    """

    module_id = "lead.followup_gate"

    def __init__(self, *, store: PostgresLeadFollowupState | Any | None = None,
                 identity_key: str | bytes | None = None) -> None:
        self._store = store or PostgresLeadFollowupState()
        configured_key = identity_key if identity_key is not None else os.getenv("FOLLOWUP_IDENTITY_HMAC_KEY")
        self._identity_key = configured_key.encode("utf-8") if isinstance(configured_key, str) else configured_key

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        error = self._validate(payload)
        if error:
            return self._failure(context, started, "INVALID_FOLLOWUP_GATE_INPUT", error,
                                 Retryability.NOT_RETRYABLE)
        if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
            return ModuleResult(
                module_id=self.module_id,
                status=ResultState.REVIEW_REQUIRED,
                business_result={"state": "PREVIEW_BLOCKED", "can_send": False,
                                 "reason": "DRY_RUN_NEVER_AUTHORISES_MESSAGES"},
                context=context,
                started_at=started,
            ).complete()
        if not isinstance(self._identity_key, bytes) or len(self._identity_key) < 32:
            return self._failure(context, started, "FOLLOWUP_IDENTITY_KEY_NOT_CONFIGURED",
                                 "A stable secret of at least 32 bytes is required to protect follow-up identities",
                                 Retryability.NOT_RETRYABLE)

        subject_key = self._digest_parts("subject", payload["subject_scope"], payload["subject_id"])
        operation = payload["operation"]
        try:
            if operation == "check":
                outcome = self._store.check(subject_key=subject_key)
            else:
                event_key = self._source_event_key(payload)
                event_type = payload["event_type"]
                expected_pause_key = self._expected_pause_key(payload) if event_type == "MANUAL_RESUME" else None
                fingerprint = self._event_fingerprint(subject_key, event_type, expected_pause_key)
                outcome = self._store.apply_event(
                    subject_key=subject_key,
                    event_key=event_key,
                    payload_fingerprint=fingerprint,
                    event_type=event_type,
                    expected_pause_key=expected_pause_key,
                )
        except FollowupEventConflict:
            return self._review(context, started, "SUPPRESSION_EVENT_CONFLICT",
                                "A stable event identity was reused with different suppression data")
        except FollowupStateNotConfigured as exc:
            return self._failure(context, started, exc.code,
                                 "Durable follow-up state is not configured; sending remains blocked",
                                 Retryability.NOT_RETRYABLE)
        except FollowupStateError as exc:
            return self._review(context, started, exc.code,
                                "Follow-up state could not be verified; sending remains blocked")
        except (OSError, TimeoutError):
            return self._review(context, started, "FOLLOWUP_STATE_UNAVAILABLE",
                                "Follow-up state could not be verified; sending remains blocked")

        state = outcome.get("state", "UNKNOWN")
        can_send = outcome.get("can_send") is True
        stale_resume = outcome.get("event_outcome") == "STALE_RESUME_REVIEW"
        recorded_event = operation == "record_event"
        status = ResultState.REVIEW_REQUIRED if stale_resume else (
            ResultState.SUCCESS if recorded_event or can_send else ResultState.REVIEW_REQUIRED
        )
        result = ModuleResult(
            module_id=self.module_id,
            status=status,
            business_result={
                "state": state,
                "can_send": can_send,
                "reason": outcome.get("reason", state),
                "event_outcome": outcome.get("event_outcome", "CHECKED"),
                "duplicate": outcome.get("duplicate", False),
            },
            metrics={"can_send": can_send, "duplicate": outcome.get("duplicate", False)},
            context=context,
            started_at=started,
        )
        if stale_resume:
            result.errors.append(ModuleError(
                code="STALE_MANUAL_RESUME",
                category="SUPPRESSION",
                message="Manual resume does not match the current pause event; follow-up remains paused",
                retryability=Retryability.NOT_RETRYABLE,
            ))
        return result.complete()

    @classmethod
    def _validate(cls, payload: Any) -> str | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("operation"), str):
            return "payload must be an object with operation=check or operation=record_event"
        operation = payload["operation"]
        if operation not in {"check", "record_event"}:
            return "operation must be check or record_event"
        base = {"operation", "subject_scope", "subject_id"}
        if operation == "check":
            if set(payload) != base:
                return "check payload contains unexpected or missing fields"
        else:
            event_fields = base | set(_SOURCE_FIELDS) | {"event_type"}
            event_type = payload.get("event_type")
            if not isinstance(event_type, str) or event_type not in _EVENT_TYPES:
                return "event_type is not a supported follow-up suppression event"
            if event_type == "MANUAL_RESUME":
                expected = payload.get("expected_pause_event")
                if not isinstance(expected, dict) or set(expected) != set(_SOURCE_FIELDS):
                    return "manual resume must identify the exact pause event it intends to clear"
                for field in _SOURCE_FIELDS:
                    issue = cls._validate_text(expected.get(field), field, _SOURCE_LIMITS[field])
                    if issue:
                        return f"expected_pause_event.{issue}"
                event_fields.add("expected_pause_event")
            if set(payload) != event_fields:
                return "record_event payload contains unexpected or missing fields"
            for field in _SOURCE_FIELDS:
                issue = cls._validate_text(payload.get(field), field, _SOURCE_LIMITS[field])
                if issue:
                    return issue
        issue = cls._validate_text(payload.get("subject_scope"), "subject_scope", 200)
        if issue:
            return issue
        return cls._validate_text(payload.get("subject_id"), "subject_id", 500)

    @staticmethod
    def _validate_text(value: Any, field: str, maximum: int) -> str | None:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            return f"{field} must be non-empty text up to {maximum} characters"
        if any(ord(char) < 32 for char in value):
            return f"{field} must not contain control characters"
        return None

    def _source_event_key(self, payload: dict[str, Any]) -> str:
        return self._digest_parts("event", *(payload[field] for field in _SOURCE_FIELDS))

    def _expected_pause_key(self, payload: dict[str, Any]) -> str:
        return self._digest_parts("event", *(payload["expected_pause_event"][field] for field in _SOURCE_FIELDS))

    @classmethod
    def _event_fingerprint(cls, subject_key: str, event_type: str, expected_pause_key: str | None) -> str:
        canonical = json.dumps(
            {"subject_key": subject_key, "event_type": event_type,
             "expected_pause_key": expected_pause_key},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _digest_parts(self, *parts: str) -> str:
        material = "\0".join(parts).encode("utf-8")
        return hmac.new(self._identity_key, material, hashlib.sha256).hexdigest()

    def _failure(self, context: ExecutionContext, started: datetime, code: str,
                 message: str, retryability: Retryability) -> ModuleResult:
        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.FAILURE,
            errors=[ModuleError(code=code, category="SUPPRESSION", message=message,
                                retryability=retryability)],
            business_result={"can_send": False, "state": "UNKNOWN"},
            context=context,
            started_at=started,
        ).complete()

    def _review(self, context: ExecutionContext, started: datetime, code: str, message: str) -> ModuleResult:
        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.REVIEW_REQUIRED,
            errors=[ModuleError(code=code, category="SUPPRESSION", message=message,
                                retryability=Retryability.UNKNOWN)],
            business_result={"can_send": False, "state": "UNKNOWN"},
            context=context,
            started_at=started,
        ).complete()
