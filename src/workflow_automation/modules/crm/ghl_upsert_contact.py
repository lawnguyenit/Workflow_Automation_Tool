from __future__ import annotations

import json
import os
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


_URL = "https://services.leadconnectorhq.com/contacts/upsert"
_CONTACT_FIELDS = {
    "firstName", "lastName", "name", "email", "phone", "address1", "city", "state",
    "postalCode", "website", "timezone", "source", "country", "companyName", "assignedTo",
    "customFields", "dnd", "inboundDndSettings", "dndSettings", "dateOfBirth", "gender",
    "createNewIfDuplicateAllowed",
}


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
    ) -> None:
        self.location_id = location_id
        self._bearer_token = bearer_token if bearer_token is not None else os.getenv("GHL_PRIVATE_INTEGRATION_TOKEN")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        contact = payload.get("contact")
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
        try:
            if self._http_client is not None:
                response = self._http_client.post(_URL, headers=headers, json=request_body,
                                                  timeout=self._timeout_seconds)
            else:
                response = httpx.post(_URL, headers=headers, json=request_body,
                                      timeout=self._timeout_seconds)
        except httpx.TimeoutException:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel request timed out; reconcile the contact before retrying",
                                Retryability.UNKNOWN)
        except httpx.RequestError:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel connection failed; reconcile the contact before retrying",
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
            return self._result(context, started, ResultState.FAILURE, "UNEXPECTED_HTTP_STATUS",
                                "HighLevel returned an unexpected response status", Retryability.UNKNOWN)

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
        for field in ("email", "phone", "firstName", "lastName", "name"):
            value = contact.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 500):
                return f"contact.{field} must be null or bounded text"
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
