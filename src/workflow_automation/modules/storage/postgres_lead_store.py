from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)

_FINGERPRINT_FIELDS = (
    "source_system", "source_account", "source_event_id", "name", "email", "phone",
    "service_requested", "description",
)
_FIELD_LIMITS = {
    "source_system": 100, "source_account": 200, "source_event_id": 250,
    "name": 250, "email": 320, "phone": 64,
    "service_requested": 500, "description": 10_000,
}
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{64}$")


class PostgresLeadStore:
    """Persist synthetic/reference lead events with a database-enforced source key.

    Exactly-once applies only to this local database row keyed by source event.
    It does not deduplicate CRM contacts or external messages.
    """

    module_id = "lead.store"

    def __init__(self, connection_factory: Callable[..., Any] | None = None) -> None:
        self._connection_factory = connection_factory

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started_at = datetime.now(UTC)
        lead = payload.get("lead")
        if not isinstance(lead, dict):
            return self._failure(context, started_at, "LEAD_OBJECT_REQUIRED", "lead must be an object",
                                 Retryability.NOT_RETRYABLE)
        validation_error = self._validate_lead(lead)
        if validation_error:
            return self._failure(context, started_at, "INVALID_LEAD_RECORD", validation_error,
                                 Retryability.NOT_RETRYABLE)
        if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
            return ModuleResult(
                module_id=self.module_id, status=ResultState.REVIEW_REQUIRED,
                business_result={"state": "PREVIEWED", "duplicate": False,
                                 "qualification": lead.get("qualification", {"status": "REVIEW_REQUIRED"})},
                context=context, started_at=started_at,
            ).complete()
        dsn = os.environ.get("DATABASE_URL")
        if not dsn and self._connection_factory is None:
            return self._failure(context, started_at, "DATABASE_NOT_CONFIGURED", "DATABASE_URL is not configured",
                                 Retryability.NOT_RETRYABLE)
        connect = self._connection_factory or psycopg.connect
        try:
            with connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO workflow_leads (
                               source_system, source_account, source_event_id, lead_data,
                               payload_fingerprint, workflow_id, run_id, correlation_id, attempt
                           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (source_system, source_account, source_event_id) DO NOTHING
                           RETURNING id""",
                        (lead["source_system"], lead["source_account"], lead["source_event_id"],
                         Jsonb(lead), lead["payload_fingerprint"], context.workflow_id, context.run_id,
                         context.correlation_id, context.attempt),
                    )
                    inserted = cur.fetchone()
                    cur.execute(
                        """SELECT id, payload_fingerprint FROM workflow_leads
                           WHERE source_system = %s AND source_account = %s AND source_event_id = %s""",
                        (lead["source_system"], lead["source_account"], lead["source_event_id"]),
                    )
                    stored = cur.fetchone()
                    if stored is None:
                        raise RuntimeError("upsert result missing")
                    duplicate = inserted is None
                    conflict = duplicate and stored[1] != lead["payload_fingerprint"]
            status = "PAYLOAD_CONFLICT_REVIEW" if conflict else ("DUPLICATE" if duplicate else "STORED")
            qualification = lead.get("qualification", {"status": "REVIEW_REQUIRED"})
            if conflict or qualification.get("status") == "REVIEW_REQUIRED":
                state = ResultState.REVIEW_REQUIRED
            else:
                state = ResultState.SUCCESS
            return ModuleResult(
                module_id=self.module_id, status=state,
                business_result={"state": status, "record_id": stored[0], "duplicate": duplicate,
                                 "payload_conflict": conflict,
                                 "qualification": qualification},
                warnings=["Same source event key arrived with a different payload; first-write-wins and manual review required."] if conflict else [],
                metrics={"stored": not duplicate, "duplicate": duplicate, "payload_conflict": conflict},
                context=context, started_at=started_at,
            ).complete()
        except (psycopg.Error, OSError, RuntimeError):
            return self._failure(context, started_at, "LEAD_STORE_FAILED",
                                 "Lead persistence failed; inspect sanitized service diagnostics",
                                 Retryability.UNKNOWN)

    def _failure(self, context: ExecutionContext, started_at: datetime, code: str, message: str,
                 retryability: Retryability) -> ModuleResult:
        return ModuleResult(
            module_id=self.module_id, status=ResultState.FAILURE,
            errors=[ModuleError(code=code, category="STORAGE", message=message,
                                retryability=retryability)],
            context=context, started_at=started_at,
        ).complete()

    @staticmethod
    def _validate_lead(lead: dict[str, Any]) -> str | None:
        expected_keys = {*_FINGERPRINT_FIELDS, "qualification", "payload_fingerprint"}
        if set(lead) != expected_keys:
            return "lead record does not match the versioned intake shape"
        for field, max_length in _FIELD_LIMITS.items():
            value = lead.get(field)
            if not isinstance(value, str) or len(value) > max_length:
                return f"{field} must be text within its declared size limit"
        if not all(lead[field] for field in ("source_system", "source_account", "source_event_id")):
            return "source event key fields must be non-empty"
        fingerprint = lead.get("payload_fingerprint")
        if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
            return "payload_fingerprint must be a lowercase SHA-256 digest"
        expected_fingerprint = hashlib.sha256(
            json.dumps({field: lead[field] for field in _FINGERPRINT_FIELDS}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if fingerprint != expected_fingerprint:
            return "payload_fingerprint does not match the lead fields"
        qualification = lead.get("qualification")
        if not isinstance(qualification, dict):
            return "qualification must be an object"
        if set(qualification) != {"status", "reasons", "classification", "summary", "model_used"}:
            return "qualification object does not match the declared intake shape"
        status = qualification.get("status")
        reasons = qualification.get("reasons")
        if not isinstance(status, str) or not status or len(status) > 64:
            return "qualification.status must be a non-empty short string"
        if (not isinstance(reasons, list) or len(reasons) > 20
                or any(not isinstance(reason, str) or len(reason) > 100 for reason in reasons)):
            return "qualification.reasons must be a list of bounded strings"
        for field, max_length in (("classification", 100), ("summary", 2_000), ("model_used", 100)):
            value = qualification.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > max_length):
                return f"qualification.{field} must be null or bounded text"
        try:
            json.dumps(lead, allow_nan=False)
        except (TypeError, ValueError):
            return "lead record must contain only JSON-safe values"
        return None
