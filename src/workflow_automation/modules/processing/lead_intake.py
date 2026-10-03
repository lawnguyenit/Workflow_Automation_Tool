from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
)


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MAX_FIELD_LENGTHS = {
    "source_system": 100,
    "source_account": 200,
    "source_event_id": 250,
    "name": 250,
    "email": 320,
    "phone": 64,
    "service_requested": 500,
    "description": 10_000,
}


class LeadIntake:
    """Normalize one submitted enquiry and fail closed to manual review.

    This module deliberately does not infer a sales score or invent absent data.
    The caller must provide a stable source event ID for idempotent persistence.
    """

    module_id = "lead.intake"

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started_at = datetime.now(UTC)
        raw = payload.get("lead")
        if not isinstance(raw, dict):
            return self._failure(context, started_at, "LEAD_OBJECT_REQUIRED", "lead must be an object")
        normalized: dict[str, str] = {}
        for field, max_length in _MAX_FIELD_LENGTHS.items():
            value = raw.get(field, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                return self._failure(context, started_at, "INVALID_FIELD_TYPE", f"{field} must be text")
            value = value.strip()
            if len(value) > max_length:
                return self._failure(context, started_at, "FIELD_TOO_LONG", f"{field} exceeds {max_length} characters")
            normalized[field] = value

        normalized["source_system"] = normalized["source_system"].lower()
        normalized["email"] = normalized["email"].lower()
        normalized["phone"] = re.sub(r"[^+0-9]", "", normalized["phone"])
        if not normalized["source_system"] or not normalized["source_account"] or not normalized["source_event_id"]:
            return self._failure(context, started_at, "SOURCE_EVENT_KEY_REQUIRED",
                                 "source_system, source_account, and source_event_id are required")

        missing = [key for key in ("name", "email", "service_requested", "description") if not normalized[key]]
        reasons = [f"missing_{field}" for field in missing]
        if normalized["email"] and not _EMAIL_RE.fullmatch(normalized["email"]):
            reasons.append("invalid_email_format")
        # No approved qualification criteria or live model are available yet.
        reasons.append("qualification_criteria_not_configured")
        fingerprint = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        lead = {
            **normalized,
            "qualification": {
                "status": "REVIEW_REQUIRED",
                "reasons": reasons,
                "classification": None,
                "summary": None,
                "model_used": None,
            },
            "payload_fingerprint": fingerprint,
        }
        return ModuleResult(
            module_id=self.module_id,
            # Intake itself succeeded, but business qualification remains unresolved.
            status=ResultState.PARTIAL,
            business_result={"lead": lead},
            warnings=["Sales qualification is held for manual review; no business rubric/model is configured."],
            metrics={"missing_required_fields": len(missing), "review_required": True},
            context=context,
            started_at=started_at,
        ).complete()

    def _failure(self, context: ExecutionContext, started_at: datetime, code: str, message: str) -> ModuleResult:
        return ModuleResult(
            module_id=self.module_id,
            status=ResultState.FAILURE,
            errors=[ModuleError(code=code, category="VALIDATION", message=message,
                                retryability=Retryability.NOT_RETRYABLE)],
            context=context,
            started_at=started_at,
        ).complete()
