from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)
from workflow_automation.modules.storage.postgres_store import NotificationOutboxError, PostgresStore


class NotifySend:
    """Persist an idempotent notification intent and optionally send to Slack.

    Mock and preview modes never contact Slack. A successful Slack response is
    recorded as SENT; a timeout is UNKNOWN and is not retried automatically.
    """

    module_id = "notify.send"

    def __init__(
        self,
        *,
        store: PostgresStore | None = None,
        webhook_url: str | None = None,
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.store = store or PostgresStore()
        self.webhook_url = webhook_url if webhook_url is not None else os.getenv("SLACK_WEBHOOK_URL")
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        records = payload.get("records")
        if not isinstance(records, list):
            return self._failure(context, started, "INVALID_RECORDS", "records must be a list",
                                 Retryability.NOT_RETRYABLE)

        results: list[dict[str, Any]] = []
        errors: list[ModuleError] = []
        for record in records:
            if not isinstance(record, dict):
                errors.append(self._error("INVALID_RECORD", "Notification record must be an object",
                                          Retryability.NOT_RETRYABLE))
                continue
            record_id = record.get("record_id")
            dedupe_id = record_id if isinstance(record_id, str) and record_id else record.get("record_key")
            if not isinstance(dedupe_id, str) or not dedupe_id:
                errors.append(self._error("MISSING_RECORD_KEY", "Record must include record_id or record_key",
                                          Retryability.NOT_RETRYABLE))
                continue
            destination = str(payload.get("destination", "slack"))
            key = hashlib.sha256(f"invoice-notification:v1:{destination}:{dedupe_id}".encode()).hexdigest()
            event = self._safe_event(record, dedupe_id)

            if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
                results.append({"record_id": record_id, "record_key": record.get("record_key"), "destination": destination,
                                "status": "PREVIEWED", "idempotency_key": key})
                continue

            try:
                intent = self.store.enqueue_notification(key, destination, event)
            except NotificationOutboxError as exc:
                errors.append(self._error(exc.code, str(exc), exc.retryability))
                continue
            except Exception:
                errors.append(self._error("OUTBOX_ERROR", "Notification intent could not be stored",
                                          Retryability.UNKNOWN))
                continue

            if context.mode == RunMode.MOCK:
                try:
                    state = str(intent["state"])
                    if state not in {"MOCKED", "SENT"}:
                        self.store.update_notification(intent["id"], "MOCKED")
                    results.append({"record_id": record_id, "destination": destination,
                                    "status": "MOCKED", "duplicate": not intent["created"],
                                    "idempotency_key": key})
                except Exception:
                    errors.append(self._error("OUTBOX_UPDATE_UNKNOWN",
                                              "Mock notification intent state could not be confirmed",
                                              Retryability.UNKNOWN))
                continue

            if context.mode != RunMode.REAL:
                errors.append(self._error("UNSUPPORTED_MODE", "Unsupported notification run mode",
                                          Retryability.NOT_RETRYABLE))
                continue

            if intent["state"] == "SENT":
                results.append({"record_id": record_id, "destination": destination,
                                "status": "SENT", "duplicate": True, "idempotency_key": key})
                continue
            if intent["state"] != "PENDING":
                errors.append(self._error(
                    "DELIVERY_REQUIRES_REVIEW",
                    f"Notification intent is {intent['state']}; automatic resend is blocked to avoid duplicates",
                    Retryability.UNKNOWN,
                ))
                continue
            if destination != "slack" or not self.webhook_url:
                errors.append(self._error("NOTIFICATION_PROVIDER_NOT_CONFIGURED",
                                          "Configure a Slack webhook before using REAL notification mode",
                                          Retryability.NOT_RETRYABLE))
                continue
            endpoint = urlparse(self.webhook_url)
            if endpoint.scheme != "https" or endpoint.hostname != "hooks.slack.com":
                errors.append(self._error("INVALID_SLACK_WEBHOOK_URL",
                                          "Slack webhook URL must use HTTPS on hooks.slack.com",
                                          Retryability.NOT_RETRYABLE))
                continue

            try:
                claimed = self.store.claim_notification(intent["id"])
            except Exception:
                errors.append(self._error("OUTBOX_UPDATE_UNKNOWN",
                                          "Could not record the notification attempt before sending",
                                          Retryability.UNKNOWN))
                continue
            if not claimed:
                errors.append(self._error("DELIVERY_ALREADY_CLAIMED",
                                          "Another run claimed this notification; automatic resend is blocked",
                                          Retryability.UNKNOWN))
                continue
            try:
                client = self.http_client
                if client is None:
                    response = httpx.post(self.webhook_url, json={"text": self._slack_text(event)},
                                          timeout=self.timeout_seconds)
                else:
                    response = client.post(self.webhook_url, json={"text": self._slack_text(event)},
                                           timeout=self.timeout_seconds)
            except httpx.TimeoutException:
                self._best_effort_state(intent["id"], "UNKNOWN", "DELIVERY_TIMEOUT")
                errors.append(self._error("DELIVERY_TIMEOUT_UNKNOWN",
                                          "Slack timed out; delivery may have occurred and won't be retried automatically",
                                          Retryability.UNKNOWN))
                continue
            except httpx.HTTPError:
                self._best_effort_state(intent["id"], "UNKNOWN", "DELIVERY_TRANSPORT_UNKNOWN")
                errors.append(self._error("DELIVERY_TRANSPORT_UNKNOWN",
                                          "Slack delivery outcome is unknown and won't be retried automatically",
                                          Retryability.UNKNOWN))
                continue

            if response.status_code == 200 and response.text.strip().lower() == "ok":
                try:
                    self.store.update_notification(intent["id"], "SENT")
                    results.append({"record_id": record_id, "destination": destination,
                                    "status": "SENT", "duplicate": False, "idempotency_key": key})
                except Exception:
                    errors.append(self._error("DELIVERY_CONFIRMED_STATE_UNKNOWN",
                                              "Slack confirmed delivery but outbox state could not be updated",
                                              Retryability.UNKNOWN))
                continue
            # The intent remains FAILED and this module has no retry scheduler or
            # delayed requeue contract, so do not promise that callers can retry it.
            retryability = Retryability.NOT_RETRYABLE
            self._best_effort_state(intent["id"], "FAILED", "SLACK_REJECTED")
            errors.append(self._error("SLACK_REJECTED",
                                      f"Slack rejected the notification (HTTP {response.status_code}); this intent needs operator reconciliation before another attempt",
                                      retryability))

        if errors and results:
            status = ResultState.PARTIAL
        elif errors:
            status = ResultState.REVIEW_REQUIRED if any(e.retryability == Retryability.UNKNOWN for e in errors) else ResultState.FAILURE
        else:
            status = ResultState.SUCCESS
        return ModuleResult(
            module_id=self.module_id,
            status=status,
            business_result={"notifications": results},
            errors=errors,
            metrics={"record_count": len(records), "notification_count": len(results), "error_count": len(errors)},
            context=context,
            started_at=started,
        ).complete()

    @staticmethod
    def _safe_event(record: dict[str, Any], record_id: str) -> dict[str, Any]:
        # Do not send invoice sender or raw document text to chat by default.
        return {
            "record_id": record_id[:128],
            "invoice_number": str(record.get("invoice_number", ""))[:128],
            "amount": str(record.get("amount", ""))[:64],
            "currency": str(record.get("currency", ""))[:3],
            "document_type": str(record.get("document_type", "invoice"))[:64],
        }

    @staticmethod
    def _slack_text(event: dict[str, Any]) -> str:
        def escape(value: Any) -> str:
            return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        return (f"Invoice processed: {escape(event['invoice_number'])} — "
                f"{escape(event['amount'])} {escape(event['currency'])} ({escape(event['record_id'])})")

    def _best_effort_state(self, notification_id: str, state: str, error_code: str) -> None:
        try:
            self.store.update_notification(notification_id, state, error_code)
        except Exception:
            pass

    @staticmethod
    def _error(code: str, message: str, retryability: Retryability) -> ModuleError:
        return ModuleError(code=code, category="NOTIFICATION", message=message,
                           retryability=retryability)

    def _failure(self, context: ExecutionContext, started: datetime, code: str, message: str,
                 retryability: Retryability) -> ModuleResult:
        return ModuleResult(module_id=self.module_id, status=ResultState.FAILURE,
                            errors=[self._error(code, message, retryability)], context=context,
                            started_at=started).complete()
