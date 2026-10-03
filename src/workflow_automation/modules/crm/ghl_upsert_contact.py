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
from workflow_automation.infrastructure.postgres_ghl_contact_upsert_ledger import (
    GHLContactAlreadyInProgress,
    GHLContactInputConflict,
    GHLContactLedgerError,
    GHLContactLedgerNotConfigured,
    GHLContactReconciliationRequired,
    PostgresGHLContactUpsertLedger,
)


_URL = "https://services.leadconnectorhq.com/contacts/upsert"
_CONTACT_FIELDS = {
    "firstName", "lastName", "name", "email", "phone", "address1", "city", "state",
    "postalCode", "website", "timezone", "source", "country", "companyName", "assignedTo",
    "customFields", "dnd", "inboundDndSettings", "dndSettings", "dateOfBirth", "gender",
    "createNewIfDuplicateAllowed",
}
_SOURCE_EVENT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,499}$")


class GHLUpsertContact:
    """Create/update one HighLevel contact through the v3 upsert endpoint.

    Dry-run and mock never make network requests. A remote timeout or server error
    is reported as uncertain because the contact write may have succeeded.
    """

    module_id = "crm.ghl_upsert_contact"

    def __init__(
        self,
        *,
        location_id: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 15.0,
        http_client: httpx.Client | None = None,
        effect_ledger: Any | None = None,
    ) -> None:
        self.location_id = location_id
        self._bearer_token = bearer_token if bearer_token is not None else os.getenv("GHL_PRIVATE_INTEGRATION_TOKEN")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._effect_ledger = effect_ledger or PostgresGHLContactUpsertLedger()

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        if not isinstance(payload, dict) or set(payload) != {"contact", "idempotency_key"}:
            return self._result(context, started, ResultState.FAILURE, "INVALID_CONTACT",
                                "payload must contain exactly contact and idempotency_key",
                                Retryability.NOT_RETRYABLE)
        contact = payload["contact"]
        idempotency_key = payload["idempotency_key"]
        if (not isinstance(idempotency_key, str) or not _SOURCE_EVENT_KEY.fullmatch(idempotency_key)
                or len(idempotency_key.split(":")) < 3):
            return self._result(context, started, ResultState.FAILURE, "INVALID_CONTACT",
                                "idempotency_key must be an opaque namespaced source-event identity",
                                Retryability.NOT_RETRYABLE)
        validation_error = self._validate_contact(contact)
        if validation_error:
            return self._result(context, started, ResultState.FAILURE, "INVALID_CONTACT", validation_error,
                                Retryability.NOT_RETRYABLE)
        if not isinstance(self.location_id, str) or not self.location_id.strip():
            return self._result(context, started, ResultState.FAILURE, "LOCATION_NOT_CONFIGURED",
                                "Configure the target HighLevel Location ID", Retryability.NOT_RETRYABLE)
        if len(self.location_id) > 500:
            return self._result(context, started, ResultState.FAILURE, "LOCATION_NOT_CONFIGURED",
                                "HighLevel Location ID exceeds the configured size limit",
                                Retryability.NOT_RETRYABLE)

        request_body = {**contact, "locationId": self.location_id}
        if context.mode == RunMode.DRY_RUN or context.mode == RunMode.REPLAY:
            return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                business_result={"state": "PREVIEWED", "side_effect": False},
                                context=context, started_at=started).complete()
        if context.mode == RunMode.MOCK:
            return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                business_result={"state": "MOCKED", "contact_id": "synthetic-contact-id",
                                                 "new": True, "side_effect": False},
                                context=context, started_at=started).complete()
        if context.mode != RunMode.REAL:
            return self._result(context, started, ResultState.FAILURE, "UNSUPPORTED_MODE",
                                "Unsupported run mode", Retryability.NOT_RETRYABLE)
        if not self._bearer_token:
            return self._result(context, started, ResultState.FAILURE, "CREDENTIAL_NOT_CONFIGURED",
                                "REAL mode requires GHL_PRIVATE_INTEGRATION_TOKEN in the runtime secret store",
                                Retryability.NOT_RETRYABLE)
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Version": "v3",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        event_key = hashlib.sha256(
            ("ghl-v3-contact-upsert\0" + self.location_id + "\0" + idempotency_key).encode("utf-8")
        ).hexdigest()
        input_fingerprint = hashlib.sha256(
            json.dumps(contact, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False).encode("utf-8")
        ).hexdigest()
        try:
            stored_result, cached = self._effect_ledger.run_once(
                event_key=event_key,
                input_fingerprint=input_fingerprint,
                operation=lambda: self._upsert(context, started, request_body, headers),
                uncertain_error_code=self._uncertain_error_code,
            )
            if cached:
                result = ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                      business_result=stored_result, context=context,
                                      started_at=started).complete()
            else:
                result = stored_result
        except GHLContactAlreadyInProgress:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "CONTACT_WRITE_IN_PROGRESS",
                                  "Another run is processing this source event; retry after it completes",
                                  Retryability.RETRYABLE)
        except GHLContactInputConflict:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "CONTACT_EVENT_PAYLOAD_CONFLICT",
                                  "This source event was already processed with different contact data; review before retrying",
                                  Retryability.NOT_RETRYABLE)
        except GHLContactReconciliationRequired:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "CONTACT_RECONCILIATION_REQUIRED",
                                  "An earlier contact write has not been reconciled; inspect HighLevel before retrying",
                                  Retryability.NOT_RETRYABLE)
        except GHLContactLedgerNotConfigured:
            result = self._result(context, started, ResultState.FAILURE, "CONTACT_LEDGER_NOT_READY",
                                  "Configure DATABASE_URL and apply migration 008 before REAL contact writes",
                                  Retryability.NOT_RETRYABLE)
        except GHLContactLedgerError:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "CONTACT_LEDGER_UNAVAILABLE",
                                  "Contact event ledger is unavailable; do not retry until database state is checked",
                                  Retryability.UNKNOWN)
        result.metrics["effect_key"] = event_key
        return result

    def _upsert(self, context: ExecutionContext, started: datetime, request_body: dict[str, Any],
                headers: dict[str, str]) -> ModuleResult:
        try:
            if self._http_client is not None:
                response = self._http_client.post(_URL, headers=headers, json=request_body,
                                                  timeout=self._timeout_seconds)
            else:
                response = httpx.post(_URL, headers=headers, json=request_body,
                                      timeout=self._timeout_seconds)
        except (httpx.TimeoutException, httpx.RequestError):
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel write outcome is unknown; reconcile the matching contact before retrying",
                                Retryability.UNKNOWN)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            result = self._result(context, started, ResultState.REVIEW_REQUIRED, "RATE_LIMITED",
                                  "HighLevel rate limit reached; retry only under the workflow's bounded backoff policy",
                                  Retryability.RETRYABLE)
            if retry_after and retry_after.isdigit():
                result.metrics["retry_after_seconds"] = int(retry_after)
            return result
        if response.status_code in (401, 403):
            return self._result(context, started, ResultState.FAILURE, "AUTHORIZATION_FAILED",
                                "HighLevel rejected the credential or required scope", Retryability.NOT_RETRYABLE)
        if response.status_code in (400, 404, 422):
            return self._result(context, started, ResultState.FAILURE, "REQUEST_REJECTED",
                                "HighLevel rejected the contact mapping or Location", Retryability.NOT_RETRYABLE)
        if response.status_code >= 500:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel returned a server error after a write request; reconcile before retrying",
                                Retryability.UNKNOWN)
        if response.status_code not in (200, 201):
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_RESPONSE_UNVERIFIED",
                                "HighLevel returned an unexpected status after a write; reconcile before retrying",
                                Retryability.UNKNOWN)

        try:
            body = response.json()
            contact_id = body["contact"]["id"]
            is_new = body["new"]
            if not isinstance(contact_id, str) or not contact_id or not isinstance(is_new, bool):
                raise (KeyError("response shape"))
        except (ValueError, KeyError, TypeError):
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_RESPONSE_UNVERIFIED",
                                "HighLevel may have written the contact, but its response could not be verified",
                                Retryability.UNKNOWN)
        return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                            business_result={"state": "UPSERTED", "contact_id": contact_id,
                                             "new": is_new, "side_effect": True},
                            context=context, started_at=started).complete()

    @staticmethod
    def _uncertain_error_code(result: ModuleResult) -> str | None:
        if result.status == ResultState.REVIEW_REQUIRED and result.errors:
            error = result.errors[0]
            if error.code in {"REMOTE_OUTCOME_UNKNOWN", "REMOTE_RESPONSE_UNVERIFIED"}:
                return error.code
        return None

    @staticmethod
    def _validate_contact(contact: Any) -> str | None:
        if not isinstance(contact, dict):
            return "payload.contact must be an object"
        if not contact:
            return "payload.contact must contain at least one mapped field"
        if "tags" in contact:
            return "tags are excluded because contact upsert replaces the full tag list; use a separate tag operation"
        unknown = set(contact) - _CONTACT_FIELDS
        if unknown:
            return "contact contains unsupported fields: " + ", ".join(sorted(unknown))
        if not any(contact.get(field) for field in ("email", "phone")):
            return "contact must include an email or phone for duplicate matching"
        duplicate_override = contact.get("createNewIfDuplicateAllowed")
        if duplicate_override is not None and not isinstance(duplicate_override, bool):
            return "contact.createNewIfDuplicateAllowed must be boolean when supplied"
        if duplicate_override is True:
            return "contact.createNewIfDuplicateAllowed=true is blocked to avoid bypassing duplicate matching"
        for field in ("email", "phone", "firstName", "lastName", "name"):
            value = contact.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 500):
                return f"contact.{field} must be null or bounded text"
            if field in {"email", "phone"} and value is not None and not value.strip():
                return f"contact.{field} must be non-empty when supplied"
        try:
            serialized = json.dumps(contact, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return "contact must contain JSON-safe values"
        if len(serialized.encode("utf-8")) > 32_768:
            return "contact mapping exceeds the 32 KiB request limit"
        return None

    def _result(self, context: ExecutionContext, started: datetime, status: ResultState,
                code: str, message: str, retryability: Retryability) -> ModuleResult:
        return ModuleResult(module_id=self.module_id, status=status,
                            errors=[ModuleError(code=code, category="CRM", message=message,
                                                retryability=retryability)],
                            context=context, started_at=started).complete()
