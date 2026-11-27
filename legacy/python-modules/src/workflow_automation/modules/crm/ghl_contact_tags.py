from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)
from workflow_automation.infrastructure.postgres_effect_guard import (
    EffectGuardBusy,
    EffectGuardNotConfigured,
    EffectGuardUnavailable,
    EffectReconciliationPending,
)


_BASE_URL = "https://services.leadconnectorhq.com/contacts"
_CONTACT_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


class GHLContactTags:
    """Add a bounded set of tags to an existing HighLevel contact.

    This deliberately avoids contact upsert's replacement semantics. REAL mode
    serializes tag writes per Location/contact through the shared PostgreSQL guard.
    """

    module_id = "crm.ghl_contact_tags"

    def __init__(
        self,
        *,
        location_id: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 15.0,
        http_client: httpx.Client | None = None,
        effect_guard: Any | None = None,
    ) -> None:
        self.location_id = location_id
        self._bearer_token = bearer_token if bearer_token is not None else os.getenv("GHL_PRIVATE_INTEGRATION_TOKEN")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._effect_guard = effect_guard

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        if not isinstance(payload, dict) or set(payload) != {"contact_id", "tags"}:
            return self._result(context, started, ResultState.FAILURE, "INVALID_CONTACT_TAGS",
                                "payload must contain exactly contact_id and tags",
                                Retryability.NOT_RETRYABLE)
        contact_id = payload["contact_id"]
        tags = payload["tags"]
        error = self._validate(contact_id, tags)
        if error:
            return self._result(context, started, ResultState.FAILURE, "INVALID_CONTACT_TAGS", error,
                                Retryability.NOT_RETRYABLE)
        tags = [tag.strip() for tag in tags]
        if not isinstance(self.location_id, str) or not self.location_id.strip() or len(self.location_id) > 500:
            return self._result(context, started, ResultState.FAILURE, "LOCATION_NOT_CONFIGURED",
                                "Configure a bounded target HighLevel Location ID",
                                Retryability.NOT_RETRYABLE)
        if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
            return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                business_result={"state": "PREVIEWED", "side_effect": False},
                                context=context, started_at=started).complete()
        if context.mode == RunMode.MOCK:
            return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                business_result={"state": "MOCKED", "side_effect": False},
                                context=context, started_at=started).complete()
        if context.mode != RunMode.REAL:
            return self._result(context, started, ResultState.FAILURE, "UNSUPPORTED_MODE",
                                "Unsupported run mode", Retryability.NOT_RETRYABLE)
        if not self._bearer_token:
            return self._result(context, started, ResultState.FAILURE, "CREDENTIAL_NOT_CONFIGURED",
                                "REAL mode requires the GHL private integration token",
                                Retryability.NOT_RETRYABLE)
        if self._effect_guard is None:
            return self._result(context, started, ResultState.FAILURE, "EFFECT_GUARD_NOT_CONFIGURED",
                                "REAL tag writes require the shared PostgreSQL effect guard",
                                Retryability.NOT_RETRYABLE)

        effect_material = "\0".join(("ghl-v3-contact-tags", self.location_id, contact_id))
        effect_key = hashlib.sha256(effect_material.encode("utf-8")).hexdigest()
        try:
            result = self._effect_guard.run(
                effect_key=effect_key,
                effect_type=self.module_id,
                correlation_id=context.correlation_id,
                operation=lambda: self._add_tags(context, started, contact_id, tags),
                uncertain_error_code=self._uncertain_error_code,
            )
        except EffectGuardBusy:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "CONTACT_TAG_WRITE_IN_PROGRESS",
                                  "Another run is updating tags for this contact; retry after it completes",
                                  Retryability.RETRYABLE)
        except EffectReconciliationPending:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "CONTACT_TAG_RECONCILIATION_REQUIRED",
                                  "An earlier tag update has an unknown outcome; inspect the contact before retrying",
                                  Retryability.NOT_RETRYABLE)
        except EffectGuardNotConfigured:
            result = self._result(context, started, ResultState.FAILURE, "EFFECT_GUARD_NOT_READY",
                                  "Configure DATABASE_URL and apply migration 003 before REAL tag writes",
                                  Retryability.NOT_RETRYABLE)
        except EffectGuardUnavailable as exc:
            status = ResultState.REVIEW_REQUIRED if exc.external_call_started else ResultState.FAILURE
            code = "EFFECT_GUARD_OUTCOME_UNKNOWN" if exc.external_call_started else "EFFECT_GUARD_UNAVAILABLE"
            retryability = Retryability.UNKNOWN if exc.external_call_started else Retryability.RETRYABLE
            result = self._result(context, started, status, code,
                                  "PostgreSQL effect guard failed; reconcile contact tags before retrying"
                                  if exc.external_call_started else
                                  "PostgreSQL effect guard is unavailable; no HighLevel write was started",
                                  retryability)
        result.metrics["effect_key"] = effect_key
        return result

    def _add_tags(self, context: ExecutionContext, started: datetime,
                  contact_id: str, tags: list[str]) -> ModuleResult:
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Version": "v3",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        url = f"{_BASE_URL}/{contact_id}/tags"
        try:
            if self._http_client is not None:
                response = self._http_client.post(url, headers=headers, json={"tags": tags},
                                                  timeout=self._timeout_seconds)
            else:
                response = httpx.post(url, headers=headers, json={"tags": tags},
                                      timeout=self._timeout_seconds)
        except (httpx.TimeoutException, httpx.RequestError):
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel tag request outcome is unknown; inspect the contact before retrying",
                                Retryability.UNKNOWN)

        if response.status_code == 429:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED, "RATE_LIMITED",
                                  "HighLevel rate limit reached; retry only under bounded backoff",
                                  Retryability.RETRYABLE)
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                result.metrics["retry_after_seconds"] = int(retry_after)
            return result
        if response.status_code in (401, 403):
            return self._result(context, started, ResultState.FAILURE, "AUTHORIZATION_FAILED",
                                "HighLevel rejected the credential or contacts.write scope",
                                Retryability.NOT_RETRYABLE)
        if response.status_code in (400, 404, 422):
            return self._result(context, started, ResultState.FAILURE, "REQUEST_REJECTED",
                                "HighLevel rejected the contact ID or tag names", Retryability.NOT_RETRYABLE)
        if response.status_code >= 500:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel returned a server error; inspect current contact tags before retrying",
                                Retryability.UNKNOWN)
        if response.status_code != 201:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_RESPONSE_UNVERIFIED",
                                "HighLevel tag result could not be confirmed; inspect the contact before retrying",
                                Retryability.UNKNOWN)
        try:
            body = response.json()
            current_tags = body["tags"]
            if not isinstance(current_tags, list) or any(not isinstance(tag, str) for tag in current_tags):
                raise (TypeError("tags"))
        except (ValueError, KeyError, TypeError):
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_RESPONSE_UNVERIFIED",
                                "HighLevel may have changed tags but returned an unverified response",
                                Retryability.UNKNOWN)
        return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                            business_result={"state": "TAGS_ADDED", "side_effect": True},
                            metrics={"requested_tag_count": len(tags)},
                            context=context, started_at=started).complete()

    @staticmethod
    def _validate(contact_id: Any, tags: Any) -> str | None:
        if not isinstance(contact_id, str) or not _CONTACT_ID.fullmatch(contact_id):
            return "contact_id must be a stable bounded HighLevel contact identifier"
        if (not isinstance(tags, list) or not 1 <= len(tags) <= 50
                or any(not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 100 for tag in tags)):
            return "tags must contain 1-50 non-empty names of at most 100 characters"
        normalized = [tag.strip() for tag in tags]
        if len(set(normalized)) != len(normalized):
            return "tags must not contain duplicate names"
        try:
            serialized = json.dumps({"tags": normalized}, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return "tags must be JSON-safe"
        if len(serialized.encode("utf-8")) > 8_192:
            return "tag request exceeds 8 KiB"
        return None

    @staticmethod
    def _uncertain_error_code(result: ModuleResult) -> str | None:
        if result.status == ResultState.REVIEW_REQUIRED and result.errors:
            if result.errors[0].code in {"REMOTE_OUTCOME_UNKNOWN", "REMOTE_RESPONSE_UNVERIFIED"}:
                return result.errors[0].code
        return None

    def _result(self, context: ExecutionContext, started: datetime, status: ResultState,
                code: str, message: str, retryability: Retryability) -> ModuleResult:
        return ModuleResult(module_id=self.module_id, status=status,
                            errors=[ModuleError(code=code, category="CRM", message=message,
                                                retryability=retryability)],
                            context=context, started_at=started).complete()
