from __future__ import annotations

import json
import hashlib
import os
import re
from datetime import UTC, date, datetime
from typing import Any

import httpx

from workflow_automation.infrastructure.postgres_effect_guard import (
    EffectGuardBusy,
    EffectGuardNotConfigured,
    EffectGuardUnavailable,
    EffectReconciliationPending,
)
from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)
from workflow_automation.infrastructure.postgres_ghl_opportunity_upsert_ledger import (
    GHLOpportunityAlreadyInProgress,
    GHLOpportunityInputConflict,
    GHLOpportunityLedgerError,
    GHLOpportunityLedgerNotConfigured,
    GHLOpportunityReconciliationRequired,
    PostgresGHLOpportunityUpsertLedger,
)


_BASE_URL = "https://services.leadconnectorhq.com"
_ALLOWED_FIELDS = {
    "contactId", "pipelineId", "pipelineStageId", "name", "status", "monetaryValue",
    "forecastExpectedCloseDate", "forecastProbability", "assignedTo", "customFields",
}
_PAGE_SIZE = 100
_MAX_SEARCH_PAGES = 10
_MAX_REQUEST_BYTES = 32_768
_SOURCE_EVENT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,499}$")


class GHLUpsertOpportunity:
    """Find one open contact/pipeline opportunity, then create or update it.

    This deliberately stops when the search is ambiguous. REAL writes require a
    shared PostgreSQL effect guard to serialize search/write and hold unknown outcomes.
    """

    module_id = "crm.ghl_upsert_opportunity"

    def __init__(
        self,
        *,
        location_id: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 15.0,
        http_client: httpx.Client | None = None,
        effect_guard: Any = None,
        event_ledger: Any | None = None,
    ) -> None:
        self.location_id = location_id
        self._bearer_token = bearer_token if bearer_token is not None else os.getenv("GHL_PRIVATE_INTEGRATION_TOKEN")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._effect_guard = effect_guard
        self._event_ledger = event_ledger or PostgresGHLOpportunityUpsertLedger()

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        if not isinstance(payload, dict) or set(payload) != {"idempotency_key", "opportunity"}:
            return self._result(context, started, ResultState.FAILURE, "INVALID_OPPORTUNITY",
                                "payload must contain exactly idempotency_key and opportunity",
                                Retryability.NOT_RETRYABLE)
        idempotency_key = payload["idempotency_key"]
        if (not isinstance(idempotency_key, str) or not _SOURCE_EVENT_KEY.fullmatch(idempotency_key)
                or len(idempotency_key.split(":")) < 3):
            return self._result(context, started, ResultState.FAILURE, "INVALID_OPPORTUNITY",
                                "idempotency_key must be an opaque namespaced source-event identity",
                                Retryability.NOT_RETRYABLE)
        opportunity = payload["opportunity"]
        validation_error = self._validate_opportunity(opportunity)
        if validation_error:
            return self._result(context, started, ResultState.FAILURE, "INVALID_OPPORTUNITY",
                                validation_error, Retryability.NOT_RETRYABLE)
        if not isinstance(self.location_id, str) or not self.location_id.strip() or len(self.location_id) > 500:
            return self._result(context, started, ResultState.FAILURE, "LOCATION_NOT_CONFIGURED",
                                "Configure a bounded target HighLevel Location ID", Retryability.NOT_RETRYABLE)
        if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
            return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                business_result={"state": "PREVIEWED", "side_effect": False},
                                context=context, started_at=started).complete()
        if context.mode == RunMode.MOCK:
            return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                business_result={"state": "MOCKED", "opportunity_id": "synthetic-opportunity-id",
                                                 "new": True, "side_effect": False},
                                context=context, started_at=started).complete()
        if context.mode != RunMode.REAL:
            return self._result(context, started, ResultState.FAILURE, "UNSUPPORTED_MODE",
                                "Unsupported run mode", Retryability.NOT_RETRYABLE)
        if not self._bearer_token:
            return self._result(context, started, ResultState.FAILURE, "CREDENTIAL_NOT_CONFIGURED",
                                "REAL mode requires the GHL private integration token in the runtime secret store",
                                Retryability.NOT_RETRYABLE)
        if self._effect_guard is None:
            return self._result(context, started, ResultState.FAILURE, "EFFECT_GUARD_NOT_CONFIGURED",
                                "REAL opportunity writes require the shared PostgreSQL effect guard",
                                Retryability.NOT_RETRYABLE)
        if self._event_ledger is None:
            return self._result(context, started, ResultState.FAILURE, "OPPORTUNITY_LEDGER_NOT_CONFIGURED",
                                "REAL opportunity writes require the shared PostgreSQL event ledger",
                                Retryability.NOT_RETRYABLE)

        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Version": "v3",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        effect_key = self._effect_key(self.location_id, opportunity["contactId"], opportunity["pipelineId"])
        event_key = hashlib.sha256(
            ("ghl-v3-opportunity-upsert\0" + self.location_id + "\0" + idempotency_key).encode("utf-8")
        ).hexdigest()
        input_fingerprint = hashlib.sha256(
            json.dumps(opportunity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False).encode("utf-8")
        ).hexdigest()
        try:
            stored_result, cached = self._event_ledger.run_once(
                event_key=event_key,
                input_fingerprint=input_fingerprint,
                operation=lambda: self._guarded_write(context, started, headers, opportunity, effect_key),
                uncertain_error_code=self._uncertain_error_code,
            )
            if cached:
                result = ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                                      business_result=stored_result, context=context,
                                      started_at=started).complete()
            else:
                result = stored_result
        except (EffectGuardBusy, GHLOpportunityAlreadyInProgress):
            result = self._result(context, started, ResultState.REVIEW_REQUIRED, "EFFECT_ALREADY_IN_PROGRESS",
                                  "Another run is handling this contact and pipeline; retry after it completes",
                                  Retryability.RETRYABLE)
        except (EffectReconciliationPending, GHLOpportunityReconciliationRequired):
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "EFFECT_RECONCILIATION_REQUIRED",
                                  "An earlier opportunity write has an unknown outcome; reconcile it before retrying",
                                  Retryability.NOT_RETRYABLE)
        except GHLOpportunityInputConflict:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "OPPORTUNITY_EVENT_PAYLOAD_CONFLICT",
                                  "This source event was already processed with different opportunity data",
                                  Retryability.NOT_RETRYABLE)
        except (EffectGuardNotConfigured, GHLOpportunityLedgerNotConfigured):
            result = self._result(context, started, ResultState.FAILURE, "EFFECT_GUARD_NOT_READY",
                                  "Configure DATABASE_URL and apply migrations 003 and 009 before REAL opportunity writes",
                                  Retryability.NOT_RETRYABLE)
        except GHLOpportunityLedgerError:
            result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                  "OPPORTUNITY_LEDGER_UNAVAILABLE",
                                  "Opportunity event ledger is unavailable; check database state before retrying",
                                  Retryability.UNKNOWN)
        except EffectGuardUnavailable as exc:
            if exc.external_call_started:
                result = self._result(context, started, ResultState.REVIEW_REQUIRED,
                                      "EFFECT_GUARD_OUTCOME_UNKNOWN",
                                      "PostgreSQL guard failed after provider work began; reconcile before retrying",
                                      Retryability.UNKNOWN)
            else:
                result = self._result(context, started, ResultState.FAILURE, "EFFECT_GUARD_UNAVAILABLE",
                                      "PostgreSQL guard is unavailable; no provider write was started",
                                      Retryability.RETRYABLE)
        result.metrics["effect_key"] = effect_key
        result.metrics["event_key"] = event_key
        return result

    def _guarded_write(self, context: ExecutionContext, started: datetime, headers: dict[str, str],
                       opportunity: dict[str, Any], effect_key: str) -> ModuleResult:
        try:
            return self._effect_guard.run(
                effect_key=effect_key,
                effect_type=self.module_id,
                correlation_id=context.correlation_id,
                operation=lambda: self._write_opportunity(context, started, headers, opportunity),
                uncertain_error_code=self._uncertain_error_code,
            )
        except EffectGuardBusy:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "EFFECT_ALREADY_IN_PROGRESS",
                                "Another run is handling this contact and pipeline; retry after it completes",
                                Retryability.RETRYABLE)
        except EffectReconciliationPending:
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "EFFECT_RECONCILIATION_REQUIRED",
                                "An earlier opportunity write has an unknown outcome; reconcile it before retrying",
                                Retryability.NOT_RETRYABLE)
        except EffectGuardNotConfigured:
            return self._result(context, started, ResultState.FAILURE, "EFFECT_GUARD_NOT_READY",
                                "Configure DATABASE_URL and apply migration 003 before REAL opportunity writes",
                                Retryability.NOT_RETRYABLE)
        except EffectGuardUnavailable as exc:
            if exc.external_call_started:
                return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                    "EFFECT_GUARD_OUTCOME_UNKNOWN",
                                    "PostgreSQL guard failed after provider work began; reconcile before retrying",
                                    Retryability.UNKNOWN)
            return self._result(context, started, ResultState.FAILURE, "EFFECT_GUARD_UNAVAILABLE",
                                "PostgreSQL guard is unavailable; no provider write was started",
                                Retryability.RETRYABLE)

    def _write_opportunity(self, context: ExecutionContext, started: datetime, headers: dict[str, str],
                           opportunity: dict[str, Any]) -> ModuleResult:
        matches, search_error = self._search_open_matches(headers, opportunity)
        if search_error is not None:
            code, message, retryability, status = search_error
            return self._result(context, started, status, code, message, retryability)
        if len(matches) > 1:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "AMBIGUOUS_OPEN_OPPORTUNITY",
                                "Multiple open opportunities match this contact and pipeline; reconcile before writing",
                                Retryability.NOT_RETRYABLE)

        is_new = len(matches) == 0
        if is_new:
            method = "POST"
            url = f"{_BASE_URL}/opportunities/"
            body = {**opportunity, "locationId": self.location_id}
        else:
            method = "PUT"
            url = f"{_BASE_URL}/opportunities/{matches[0]['id']}"
            body = {key: value for key, value in opportunity.items() if key != "contactId"}

        try:
            response = self._send(method, url, headers=headers, body=body)
        except httpx.TimeoutException:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel write timed out; reconcile the opportunity before retrying",
                                Retryability.UNKNOWN)
        except httpx.RequestError:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_OUTCOME_UNKNOWN",
                                "HighLevel connection failed during a write; reconcile before retrying",
                                Retryability.UNKNOWN)

        failure = self._write_failure(response)
        if failure is not None:
            code, message, retryability, status = failure
            return self._result(context, started, status, code, message, retryability)
        try:
            response_body = response.json()
            opportunity_id = response_body["opportunity"]["id"]
            if not isinstance(opportunity_id, str) or not opportunity_id:
                raise (KeyError("id"))
        except (ValueError, KeyError, TypeError):
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "REMOTE_RESPONSE_UNVERIFIED",
                                "HighLevel may have written the opportunity, but its response could not be verified",
                                Retryability.UNKNOWN)
        return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                            business_result={"state": "UPSERTED", "opportunity_id": opportunity_id,
                                             "new": is_new, "side_effect": True},
                            context=context, started_at=started).complete()

    @staticmethod
    def _effect_key(location_id: str, contact_id: str, pipeline_id: str) -> str:
        raw_key = "\0".join(("ghl-v3-open-opportunity", location_id, contact_id, pipeline_id))
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _uncertain_error_code(result: ModuleResult) -> str | None:
        if result.status == ResultState.REVIEW_REQUIRED and result.errors:
            error = result.errors[0]
            if error.code in {"REMOTE_OUTCOME_UNKNOWN", "REMOTE_RESPONSE_UNVERIFIED",
                              "EFFECT_GUARD_OUTCOME_UNKNOWN", "EFFECT_RECONCILIATION_REQUIRED"}:
                return error.code
        return None

    def _search_open_matches(self, headers: dict[str, str], opportunity: dict[str, Any]):
        matches: list[dict[str, Any]] = []
        for page in range(1, _MAX_SEARCH_PAGES + 1):
            params = {
                "locationId": self.location_id,
                "pipelineId": opportunity["pipelineId"],
                "contactId": opportunity["contactId"],
                "status": "open",
                "page": page,
                "limit": _PAGE_SIZE,
            }
            try:
                response = self._send("GET", f"{_BASE_URL}/opportunities/search", headers=headers,
                                      params=params)
            except httpx.TimeoutException:
                return None, ("SEARCH_UNAVAILABLE", "HighLevel opportunity search timed out before any write",
                              Retryability.RETRYABLE, ResultState.FAILURE)
            except httpx.RequestError:
                return None, ("SEARCH_UNAVAILABLE", "HighLevel opportunity search failed before any write",
                              Retryability.RETRYABLE, ResultState.FAILURE)

            failure = self._search_failure(response)
            if failure is not None:
                return None, failure
            try:
                body = response.json()
                page_items = body["opportunities"]
                if not isinstance(page_items, list):
                    raise TypeError("opportunities")
                meta = body.get("meta", {})
                if not isinstance(meta, dict):
                    raise TypeError("meta")
                if any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                       or not item["id"] for item in page_items):
                    raise TypeError("opportunity id")
            except (ValueError, KeyError, TypeError):
                return None, ("SEARCH_RESPONSE_UNVERIFIED", "HighLevel search response could not be verified",
                              Retryability.UNKNOWN, ResultState.REVIEW_REQUIRED)
            matches.extend(page_items)
            total = meta.get("total")
            has_more = bool(meta.get("nextPageUrl")) or (
                isinstance(total, int) and not isinstance(total, bool) and total > len(matches)
            ) or len(page_items) == _PAGE_SIZE
            if not has_more:
                return matches, None
            if page == _MAX_SEARCH_PAGES:
                break
        return None, ("SEARCH_RESULT_LIMIT_REACHED", "HighLevel search exceeded the safe page limit; no write was made",
                      Retryability.NOT_RETRYABLE, ResultState.REVIEW_REQUIRED)

    def _send(self, method: str, url: str, *, headers: dict[str, str],
              params: dict[str, Any] | None = None, body: dict[str, Any] | None = None):
        if self._http_client is not None:
            return self._http_client.request(method, url, headers=headers, params=params, json=body,
                                             timeout=self._timeout_seconds)
        return httpx.request(method, url, headers=headers, params=params, json=body,
                             timeout=self._timeout_seconds)

    @staticmethod
    def _search_failure(response: httpx.Response):
        if response.status_code == 429:
            return ("RATE_LIMITED", "HighLevel rate limit reached during a read-only search",
                    Retryability.RETRYABLE, ResultState.REVIEW_REQUIRED)
        if response.status_code in (401, 403):
            return ("AUTHORIZATION_FAILED", "HighLevel rejected the credential or opportunity read scope",
                    Retryability.NOT_RETRYABLE, ResultState.FAILURE)
        if response.status_code in (400, 404, 422):
            return ("SEARCH_REJECTED", "HighLevel rejected the opportunity search mapping",
                    Retryability.NOT_RETRYABLE, ResultState.FAILURE)
        if response.status_code >= 500:
            return ("SEARCH_UNAVAILABLE", "HighLevel search returned a server error before any write",
                    Retryability.RETRYABLE, ResultState.FAILURE)
        if response.status_code != 200:
            return ("UNEXPECTED_HTTP_STATUS", "HighLevel returned an unexpected opportunity search status",
                    Retryability.UNKNOWN, ResultState.REVIEW_REQUIRED)
        return None

    @staticmethod
    def _write_failure(response: httpx.Response):
        if response.status_code == 429:
            return ("RATE_LIMITED", "HighLevel rate limit reached; retry only under bounded backoff",
                    Retryability.RETRYABLE, ResultState.REVIEW_REQUIRED)
        if response.status_code in (401, 403):
            return ("AUTHORIZATION_FAILED", "HighLevel rejected the credential or opportunity write scope",
                    Retryability.NOT_RETRYABLE, ResultState.FAILURE)
        if response.status_code in (400, 404, 422):
            return ("REQUEST_REJECTED", "HighLevel rejected the opportunity mapping or target record",
                    Retryability.NOT_RETRYABLE, ResultState.FAILURE)
        if response.status_code >= 500:
            return ("REMOTE_OUTCOME_UNKNOWN", "HighLevel returned a server error after a write; reconcile before retrying",
                    Retryability.UNKNOWN, ResultState.REVIEW_REQUIRED)
        if response.status_code not in (200, 201):
            return ("UNEXPECTED_HTTP_STATUS", "HighLevel returned an unexpected opportunity write status",
                    Retryability.UNKNOWN, ResultState.REVIEW_REQUIRED)
        return None

    @staticmethod
    def _validate_opportunity(opportunity: Any) -> str | None:
        if not isinstance(opportunity, dict):
            return "payload.opportunity must be an object"
        required = {"contactId", "pipelineId", "pipelineStageId", "name", "status"}
        missing = required - opportunity.keys()
        if missing:
            return "opportunity is missing required fields: " + ", ".join(sorted(missing))
        unknown = opportunity.keys() - _ALLOWED_FIELDS
        if unknown:
            return "opportunity contains unsupported fields: " + ", ".join(sorted(unknown))
        for field in ("contactId", "pipelineId", "pipelineStageId", "name"):
            value = opportunity.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                return f"opportunity.{field} must be bounded non-empty text"
        status = opportunity.get("status")
        if not isinstance(status, str) or status != "open":
            return "this upsert handles open opportunities only; status must be open"
        value = opportunity.get("monetaryValue")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or value < 0):
            return "opportunity.monetaryValue must be a non-negative number"
        probability = opportunity.get("forecastProbability")
        if probability is not None and (isinstance(probability, bool) or not isinstance(probability, (int, float))
                                        or not 0 <= probability <= 100):
            return "opportunity.forecastProbability must be a number from 0 to 100"
        expected_close = opportunity.get("forecastExpectedCloseDate")
        if expected_close is not None and (not isinstance(expected_close, str) or not expected_close.strip()
                                           or len(expected_close) > 100):
            return "opportunity.forecastExpectedCloseDate must be bounded text"
        if isinstance(expected_close, str):
            try:
                date.fromisoformat(expected_close)
            except ValueError:
                try:
                    datetime.fromisoformat(expected_close.replace("Z", "+00:00"))
                except ValueError:
                    return "opportunity.forecastExpectedCloseDate must be an ISO 8601 date or timestamp"
        assigned_to = opportunity.get("assignedTo")
        if assigned_to is not None and (not isinstance(assigned_to, str) or len(assigned_to) > 500):
            return "opportunity.assignedTo must be bounded text"
        custom_fields = opportunity.get("customFields")
        if custom_fields is not None and not isinstance(custom_fields, list):
            return "opportunity.customFields must be an array"
        if isinstance(custom_fields, list) and (len(custom_fields) > 100 or any(
            not isinstance(item, dict) or set(item) != {"id", "fieldValue"}
            or not isinstance(item["id"], str) or not item["id"] or len(item["id"]) > 500
            for item in custom_fields
        )):
            return "opportunity.customFields must contain bounded {id, fieldValue} objects"
        try:
            serialized = json.dumps(opportunity, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return "opportunity must contain JSON-safe values"
        if len(serialized.encode("utf-8")) > _MAX_REQUEST_BYTES:
            return "opportunity mapping exceeds the 32 KiB request limit"
        return None

    def _result(self, context: ExecutionContext, started: datetime, status: ResultState,
                code: str, message: str, retryability: Retryability) -> ModuleResult:
        return ModuleResult(module_id=self.module_id, status=status,
                            errors=[ModuleError(code=code, category="CRM", message=message,
                                                retryability=retryability)],
                            context=context, started_at=started).complete()
