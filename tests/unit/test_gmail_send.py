import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.storage.postgres_store import NotificationOutboxError
from workflow_automation.modules.output.gmail_send import GmailSend


class _Store:
    def __init__(self):
        self.intent = None
        self.redactions = 0

    def enqueue_notification(self, key, destination, payload):
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(f"{destination}\n{canonical}".encode()).hexdigest()
        if self.intent is None:
            self.intent = {"id": "41", "created": True, "state": "PENDING",
                           "key": key, "destination": destination, "payload": payload,
                           "fingerprint": fingerprint}
        elif self.intent["key"] != key or self.intent["destination"] != destination or self.intent["fingerprint"] != fingerprint:
            raise NotificationOutboxError("OUTBOX_IDEMPOTENCY_KEY_REUSED", "safe", "NOT_RETRYABLE")
        else:
            self.intent["created"] = False
        return {key: self.intent[key] for key in ("id", "created", "state")}

    def claim_notification(self, _notification_id):
        if self.intent["state"] != "PENDING":
            return False
        self.intent["state"] = "SENDING"
        return True

    def update_notification(self, _notification_id, state, _error_code=None):
        self.intent["state"] = state
        return {"id": "41", "state": state}

    def redact_sent_notification(self, _notification_id):
        assert self.intent["state"] == "SENT"
        self.intent["payload"] = {}
        self.redactions += 1
        return True


def _context(mode=RunMode.REAL):
    return ExecutionContext(workflow_id="gmail_send_test", mode=mode)


def _payload(**changes):
    return {"idempotency_key": "source-event-123:ack", "to": "lawnguyenit@gmail.com",
            "subject": "Synthetic acknowledgement", "text_body": "We received your test enquiry.", **changes}


def _module(handler, *, store=None, token="synthetic-token"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    module = GmailSend(store=store or _Store(), account_scope="test-account", access_token=token,
                       http_client=client)
    return module, client


def test_sends_one_plain_text_message_and_redacts_outbox_after_provider_confirmation():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/gmail/v1/users/me/messages/send"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        raw = request.read()
        assert len(raw) < 32_768
        raw_message = json.loads(raw)["raw"]
        padded = raw_message + "=" * (-len(raw_message) % 4)
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(padded))
        assert message["To"] == "lawnguyenit@gmail.com"
        assert message["Subject"] == "Synthetic acknowledgement"
        assert message.get_content().strip() == "We received your test enquiry."
        return httpx.Response(200, json={"id": "gmail-message-123", "threadId": "thread-123"})

    store = _Store()
    module, client = _module(handler, store=store)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "SUCCESS"
    assert result.business_result == {"state": "SENT", "message_id": "gmail-message-123",
                                      "duplicate": False, "side_effect": True}
    assert len(calls) == 1
    assert store.intent["state"] == "SENT"
    assert store.intent["payload"] == {}
    assert store.redactions == 1
    assert "lawnguyenit@gmail.com" not in str(result.model_dump())
    assert "test enquiry" not in str(result.model_dump())


def test_duplicate_after_confirmation_never_sends_again_and_same_key_changed_content_fails():
    calls = []

    def handler(_request):
        calls.append(True)
        return httpx.Response(200, json={"id": "gmail-message-123"})

    store = _Store()
    module, client = _module(handler, store=store)
    try:
        first = module.execute(_payload(), _context())
        duplicate = module.execute(_payload(), _context())
        changed = module.execute(_payload(text_body="Changed body"), _context())
    finally:
        client.close()
    assert first.status == duplicate.status == "SUCCESS"
    assert duplicate.business_result["duplicate"] is True
    assert duplicate.business_result["message_id"] is None
    assert changed.status == "REVIEW_REQUIRED"
    assert changed.errors[0].code == "OUTBOX_IDEMPOTENCY_KEY_REUSED"
    assert len(calls) == 1


def test_unknown_send_outcome_is_held_without_a_second_provider_call():
    calls = []

    def handler(_request):
        calls.append(True)
        raise httpx.ReadTimeout("synthetic timeout")

    store = _Store()
    module, client = _module(handler, store=store)
    try:
        first = module.execute(_payload(), _context())
        second = module.execute(_payload(), _context())
    finally:
        client.close()
    assert first.status == "REVIEW_REQUIRED"
    assert first.errors[0].code == "DELIVERY_TIMEOUT_UNKNOWN"
    assert second.errors[0].code == "DELIVERY_REQUIRES_REVIEW"
    assert store.intent["state"] == "UNKNOWN"
    assert len(calls) == 1


def test_rate_limit_returns_to_pending_and_exposes_bounded_retry_after():
    calls = []

    def handler(_request):
        calls.append(True)
        return httpx.Response(429, headers={"Retry-After": "12"})

    store = _Store()
    module, client = _module(handler, store=store)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.errors[0].code == "RATE_LIMITED"
    assert result.errors[0].details == {"retry_after_seconds": 12}
    assert store.intent["state"] == "PENDING"
    assert calls


def test_rate_limit_accepts_http_date_retry_after():
    retry_at = datetime.now(UTC) + timedelta(seconds=40)
    module, client = _module(lambda _request: httpx.Response(
        429, headers={"Retry-After": format_datetime(retry_at, usegmt=True)}
    ))
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    delay = result.errors[0].details["retry_after_seconds"]
    assert 0 <= delay <= 40
    assert delay >= 35


def test_auth_rejection_is_non_retryable_and_recorded_as_failed():
    store = _Store()
    module, client = _module(lambda _request: httpx.Response(401), store=store)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "FAILURE"
    assert result.errors[0].code == "AUTHORIZATION_FAILED"
    assert result.errors[0].retryability == "NOT_RETRYABLE"
    assert store.intent["state"] == "FAILED"


def test_rate_limit_with_failed_outbox_transition_requires_review():
    class FailingUpdateStore(_Store):
        def update_notification(self, *_args):
            raise RuntimeError("synthetic database failure")

    calls = []
    module, client = _module(lambda _request: (calls.append(True) or httpx.Response(429)),
                             store=FailingUpdateStore())
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "REVIEW_REQUIRED"
    assert result.errors[0].code == "OUTBOX_STATE_UNKNOWN"
    assert result.errors[0].retryability == "UNKNOWN"
    assert calls == [True]


def test_malformed_success_response_is_held_for_reconciliation():
    store = _Store()
    module, client = _module(lambda _request: httpx.Response(200, json={"threadId": "no-id"}),
                             store=store)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "REVIEW_REQUIRED"
    assert result.errors[0].code == "REMOTE_RESPONSE_UNVERIFIED"
    assert store.intent["state"] == "UNKNOWN"


@pytest.mark.parametrize("payload", [
    _payload(to="one@example.com, two@example.com"),
    _payload(to="Display Name <person@example.com>"),
    _payload(to="a..b@example.com"),
    _payload(idempotency_key="   "),
    _payload(subject="bad\r\nBcc: person@example.com"),
    _payload(text_body="x" * 8_193),
    {"idempotency_key": "k", "to": "a@example.com", "subject": "x", "extra": "not-allowed"},
])
def test_invalid_messages_are_rejected_before_outbox_or_network(payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "unexpected"})

    store = _Store()
    module, client = _module(handler, store=store)
    try:
        result = module.execute(payload, _context())
    finally:
        client.close()
    assert result.errors[0].code == "INVALID_MESSAGE"
    assert calls == []
    assert store.intent is None


def test_preview_and_mock_modes_make_no_database_or_network_calls():
    def handler(_request):
        raise AssertionError("provider must not be called")

    class Store:
        def enqueue_notification(self, *_args):
            raise AssertionError("outbox must not be called")

    module, client = _module(handler, store=Store())
    try:
        preview = module.execute(_payload(), _context(RunMode.DRY_RUN))
        mocked = module.execute(_payload(), _context(RunMode.MOCK))
    finally:
        client.close()
    assert preview.business_result["state"] == "PREVIEWED"
    assert mocked.business_result["state"] == "MOCKED"
    assert preview.business_result["side_effect"] is False
    assert mocked.business_result["side_effect"] is False


def test_real_mode_requires_gmail_send_credential():
    module, client = _module(lambda _request: httpx.Response(200, json={"id": "x"}), token=None)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.errors[0].code == "CREDENTIAL_NOT_CONFIGURED"


def test_account_scope_must_not_be_an_email_or_arbitrary_identifier():
    module, client = _module(lambda _request: httpx.Response(200, json={"id": "x"}))
    module.account_scope = "lawnguyenit@gmail.com"
    try:
        result = module.execute(_payload(), _context(RunMode.DRY_RUN))
    finally:
        client.close()
    assert result.errors[0].code == "INVALID_ACCOUNT_SCOPE"
