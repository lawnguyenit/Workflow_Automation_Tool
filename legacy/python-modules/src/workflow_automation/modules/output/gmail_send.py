from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
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
from workflow_automation.modules.storage.postgres_store import (
    NotificationOutboxError,
    PostgresStore,
)


_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_ACCOUNT_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_MAX_BODY_BYTES = 8_192
_MAX_REQUEST_BYTES = 32_768
_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1"


class GmailSend:
    """Send one plain-text Gmail message through a durable, source-keyed outbox.

    Gmail API writes do not expose an idempotency token. The outbox therefore
    sends once per caller key, blocks in-flight/unknown outcomes, and redacts the
    stored message payload after delivery is confirmed.
    """

    module_id = "gmail.send"

    def __init__(
        self,
        *,
        store: PostgresStore | None = None,
        account_scope: str = "default-gmail",
        access_token: str | None = None,
        timeout_seconds: float = 15.0,
        http_client: httpx.Client | None = None,
        api_base_url: str = _API_BASE_URL,
    ) -> None:
        self.store = store or PostgresStore()
        self.account_scope = account_scope
        self.access_token = access_token if access_token is not None else os.getenv("GOOGLE_GMAIL_ACCESS_TOKEN")
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client
        self.api_base_url = api_base_url.rstrip("/")

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        started = datetime.now(UTC)
        error = self._validate(payload)
        if error:
            return self._result(context, started, ResultState.FAILURE, "INVALID_MESSAGE", error,
                                Retryability.NOT_RETRYABLE)
        if not isinstance(self.account_scope, str) or not _ACCOUNT_SCOPE_RE.fullmatch(self.account_scope):
            return self._result(context, started, ResultState.FAILURE, "INVALID_ACCOUNT_SCOPE",
                                "account_scope must be a non-sensitive stable identifier",
                                Retryability.NOT_RETRYABLE)

        key = hashlib.sha256(
            f"gmail-send-v1\0{self.account_scope}\0{payload['idempotency_key']}".encode("utf-8")
        ).hexdigest()
        intent_payload = {
            "to": payload["to"],
            "subject": payload["subject"],
            "text_body": payload["text_body"],
        }
        destination = f"gmail:{self.account_scope}"

        if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY):
            return self._success(context, started, "PREVIEWED", None, False,
                                 key, side_effect=False)
        if context.mode == RunMode.MOCK:
            return self._success(context, started, "MOCKED", None, False,
                                 key, side_effect=False)
        if context.mode != RunMode.REAL:
            return self._result(context, started, ResultState.FAILURE, "UNSUPPORTED_MODE",
                                "Unsupported run mode", Retryability.NOT_RETRYABLE)
        if not self.access_token:
            return self._result(context, started, ResultState.FAILURE, "CREDENTIAL_NOT_CONFIGURED",
                                "REAL mode requires a Gmail OAuth access token with gmail.send scope",
                                Retryability.NOT_RETRYABLE)

        try:
            intent = self.store.enqueue_notification(key, destination, intent_payload)
        except NotificationOutboxError as exc:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, exc.code,
                                str(exc), exc.retryability)
        except Exception:
            return self._result(context, started, ResultState.REVIEW_REQUIRED, "OUTBOX_ERROR",
                                "Gmail send intent could not be safely recorded", Retryability.UNKNOWN)

        if intent["state"] == "SENT":
            try:
                redacted = self.store.redact_sent_notification(intent["id"])
            except Exception:
                return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                    "OUTBOX_REDACTION_FAILED",
                                    "Message was already accepted; stored message content could not be cleared",
                                    Retryability.UNKNOWN,
                                    metrics={"idempotency_key": key, "side_effect": True})
            if not redacted:
                return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                    "OUTBOX_REDACTION_FAILED",
                                    "Message was already accepted; outbox state did not allow content clearing",
                                    Retryability.UNKNOWN,
                                    metrics={"idempotency_key": key, "side_effect": True})
            return self._success(context, started, "SENT", None, True, key, side_effect=True)
        if intent["state"] != "PENDING":
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "DELIVERY_REQUIRES_REVIEW",
                                f"Gmail intent is {intent['state']}; automatic resend is blocked",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": intent["state"] in {"SENDING", "UNKNOWN", "SENT"}})

        try:
            claimed = self.store.claim_notification(intent["id"])
        except NotificationOutboxError as exc:
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                exc.code, str(exc), exc.retryability)
        if not claimed:
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "DELIVERY_ALREADY_CLAIMED",
                                "Another run claimed this Gmail message; automatic resend is blocked",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": False})

        message = EmailMessage(policy=SMTP)
        message["To"] = payload["to"]
        message["Subject"] = payload["subject"]
        message.set_content(payload["text_body"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        if len(raw.encode("ascii")) > _MAX_REQUEST_BYTES:
            if not self._update_state(intent["id"], "FAILED", "MESSAGE_TOO_LARGE"):
                return self._outbox_state_unknown(context, started, key)
            return self._result(context, started, ResultState.FAILURE, "MESSAGE_TOO_LARGE",
                                "Encoded Gmail message exceeds the request limit",
                                Retryability.NOT_RETRYABLE)

        try:
            response = self._send(raw)
        except httpx.TimeoutException:
            self._update_state(intent["id"], "UNKNOWN", "DELIVERY_TIMEOUT")
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "DELIVERY_TIMEOUT_UNKNOWN",
                                "Gmail timed out; delivery may have occurred and will not be retried automatically",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": True})
        except httpx.RequestError:
            self._update_state(intent["id"], "UNKNOWN", "DELIVERY_TRANSPORT_UNKNOWN")
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "DELIVERY_TRANSPORT_UNKNOWN",
                                "Gmail connection failed during send; reconcile before retrying",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": True})

        if response.status_code in (401, 403):
            if not self._update_state(intent["id"], "FAILED", "AUTHORIZATION_FAILED"):
                return self._outbox_state_unknown(context, started, key)
            return self._result(context, started, ResultState.FAILURE, "AUTHORIZATION_FAILED",
                                "Gmail rejected the credential or gmail.send permission",
                                Retryability.NOT_RETRYABLE)
        if response.status_code == 429:
            retry_after = self._retry_after(response)
            if not self._update_state(intent["id"], "PENDING", "RATE_LIMITED"):
                return self._outbox_state_unknown(context, started, key)
            details = {"retry_after_seconds": retry_after} if retry_after is not None else {}
            return self._result(context, started, ResultState.FAILURE, "RATE_LIMITED",
                                "Gmail rate limit reached; retry only after the provider delay",
                                Retryability.RETRYABLE, details=details,
                                metrics={"idempotency_key": key, "side_effect": False})
        if response.status_code in (400, 404, 422):
            if not self._update_state(intent["id"], "FAILED", "REQUEST_REJECTED"):
                return self._outbox_state_unknown(context, started, key)
            return self._result(context, started, ResultState.FAILURE, "REQUEST_REJECTED",
                                "Gmail rejected the message or sender configuration",
                                Retryability.NOT_RETRYABLE)
        if response.status_code >= 500 or response.status_code not in (200, 201):
            self._update_state(intent["id"], "UNKNOWN", "REMOTE_OUTCOME_UNKNOWN")
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "REMOTE_OUTCOME_UNKNOWN",
                                "Gmail may have accepted the message; reconcile before retrying",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": True})

        try:
            body = response.json()
            message_id = body["id"]
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("missing message id")
        except (ValueError, KeyError, TypeError):
            self._update_state(intent["id"], "UNKNOWN", "REMOTE_RESPONSE_UNVERIFIED")
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "REMOTE_RESPONSE_UNVERIFIED",
                                "Gmail may have accepted the message but returned an unverifiable response",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": True})

        try:
            self.store.update_notification(intent["id"], "SENT")
        except Exception:
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "OUTBOX_CONFIRMATION_UNKNOWN",
                                "Gmail accepted the message but PostgreSQL did not confirm its sent state",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": True})
        try:
            redacted = self.store.redact_sent_notification(intent["id"])
        except Exception:
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "OUTBOX_REDACTION_FAILED",
                                "Gmail accepted the message; stored message content could not be cleared",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": True})
        if not redacted:
            return self._result(context, started, ResultState.REVIEW_REQUIRED,
                                "OUTBOX_REDACTION_FAILED",
                                "Gmail accepted the message; outbox state did not allow content clearing",
                                Retryability.UNKNOWN,
                                metrics={"idempotency_key": key, "side_effect": True})
        return self._success(context, started, "SENT", message_id, not intent["created"], key,
                             side_effect=True)

    def _send(self, raw: str) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        url = f"{self.api_base_url}/users/me/messages/send"
        client = self.http_client
        if client is not None:
            return client.post(url, headers=headers, json={"raw": raw}, timeout=self.timeout_seconds)
        return httpx.post(url, headers=headers, json={"raw": raw}, timeout=self.timeout_seconds)

    def _update_state(self, notification_id: str, state: str, error_code: str) -> bool:
        try:
            self.store.update_notification(notification_id, state, error_code)
            return True
        except Exception:
            # The outbox may remain SENDING. That state blocks another send until
            # an operator checks Gmail and the database; never retry blindly.
            return False

    @staticmethod
    def _outbox_state_unknown(context: ExecutionContext, started: datetime, key: str) -> ModuleResult:
        return GmailSend._result(
            context, started, ResultState.REVIEW_REQUIRED, "OUTBOX_STATE_UNKNOWN",
            "Gmail response was received but PostgreSQL could not record the safe retry state; reconcile before retrying",
            Retryability.UNKNOWN, metrics={"idempotency_key": key, "side_effect": False},
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            seconds = int(value)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                seconds = int((retry_at - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None
        return min(max(seconds, 0), 86_400)

    @staticmethod
    def _validate(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return "payload must be an object"
        if set(payload) != {"idempotency_key", "to", "subject", "text_body"}:
            return "payload must contain exactly idempotency_key, to, subject, and text_body"
        key, recipient, subject, body = (
            payload["idempotency_key"], payload["to"], payload["subject"], payload["text_body"]
        )
        if not isinstance(key, str) or not 1 <= len(key.strip()) <= 200 or not key.strip():
            return "idempotency_key must be 1 to 200 characters"
        if not isinstance(recipient, str) or len(recipient) > 320 or not _EMAIL_RE.fullmatch(recipient):
            return "to must be one bounded email address"
        try:
            parsed_address = Address(addr_spec=recipient).addr_spec
        except (TypeError, ValueError):
            return "to must contain exactly one syntactically valid email address"
        if parsed_address != recipient or recipient.count("@") != 1:
            return "to must contain exactly one email address without a display name"
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 200:
            return "subject must contain 1 to 200 characters"
        if "\r" in subject or "\n" in subject:
            return "subject must not contain line breaks"
        if not isinstance(body, str) or not body.strip():
            return "text_body must be non-empty text"
        try:
            if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
                return "text_body exceeds the 8 KiB limit"
        except UnicodeEncodeError:
            return "text_body must be valid UTF-8 text"
        return None

    @staticmethod
    def _success(context: ExecutionContext, started: datetime, state: str,
                 message_id: str | None, duplicate: bool, key: str, *, side_effect: bool) -> ModuleResult:
        return ModuleResult(
            module_id=GmailSend.module_id,
            status=ResultState.SUCCESS,
            business_result={"state": state, "message_id": message_id, "duplicate": duplicate,
                             "side_effect": side_effect},
            metrics={"idempotency_key": key, "side_effect": side_effect},
            context=context,
            started_at=started,
        ).complete()

    @staticmethod
    def _result(context: ExecutionContext, started: datetime, status: ResultState,
                code: str, message: str, retryability: Retryability, *,
                details: dict[str, Any] | None = None,
                metrics: dict[str, int | float | str | bool] | None = None) -> ModuleResult:
        return ModuleResult(
            module_id=GmailSend.module_id,
            status=status,
            errors=[ModuleError(code=code, category="EMAIL", message=message,
                                retryability=retryability, details=details or {})],
            metrics=metrics or {},
            context=context,
            started_at=started,
        ).complete()
