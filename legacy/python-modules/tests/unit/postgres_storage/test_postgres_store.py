from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import psycopg

from workflow_automation.contracts import ExecutionContext, ResultState, Retryability, RunMode
from workflow_automation.modules.storage.postgres_store import (
    NotificationOutboxError,
    PostgresStore,
)


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []
        self.lastrow = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.executed.append((sql, params))
        self.lastrow = self.rows.pop(0) if self.rows else None

    def fetchone(self):
        return self.lastrow


class FakeConnection:
    def __init__(self, rows=()):
        self.cursor_instance = FakeCursor(rows)
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_instance


def context():
    return ExecutionContext(workflow_id="invoice-intake", run_id=str(uuid4()))


def record(**overrides):
    item = {
        "record_key": "legacy-not-used-for-dedup",
        "source_system": "gmail_fixture",
        "source_account": "fixture-mailbox",
        "source_message_id": "message-1",
        "source_attachment_id": "attachment-1",
        "document_type": "invoice",
        "sender": "Acme Example",
        "invoice_number": "INV-1001",
        "amount": "1200.00",
        "currency": "USD",
        "status": "EXTRACTED",
        "confidence": 0.98,
        "extraction_provider": "fixture",
        "extraction_model": "deterministic-v1",
    }
    item.update(overrides)
    return item


def test_insert_returns_canonical_id_and_source_key_scope():
    connection = FakeConnection(rows=[(42,)])
    result = PostgresStore(lambda **_: connection).execute({"records": [record()]}, context())

    assert result.status == ResultState.SUCCESS
    assert result.business_result["records"] == [
        {**record(), "record_id": "42", "duplicate": False}
    ]
    insert_sql, params = connection.cursor_instance.executed[0]
    assert "ON CONFLICT (source_system, source_account, source_message_id, source_attachment_id)" in insert_sql
    assert params[:4] == ("gmail_fixture", "fixture-mailbox", "message-1", "attachment-1")
    assert result.metrics["inserted_count"] == 1


def test_duplicate_returns_existing_id_and_flags_changed_fields_without_overwrite():
    connection = FakeConnection(rows=[None, (42, "old-fingerprint")])
    changed = record(amount="1500.00")
    result = PostgresStore(lambda **_: connection).execute({"records": [changed]}, context())

    assert result.status == ResultState.SUCCESS
    assert result.business_result["records"][0]["record_id"] == "42"
    assert result.business_result["records"][0]["duplicate"] is True
    assert result.metrics["duplicate_changed_count"] == 1
    assert any("changed normalized fields" in warning for warning in result.warnings)


def test_missing_attachment_uses_email_level_dedupe_key():
    connection = FakeConnection(rows=[(43,)])
    result = PostgresStore(lambda **_: connection).execute(
        {"records": [record(source_attachment_id=None)]}, context()
    )

    assert result.status == ResultState.SUCCESS
    assert connection.cursor_instance.executed[0][1][3] == ""
    assert any("single document" in warning for warning in result.warnings)


def test_missing_account_uses_explicit_configured_scope():
    item = record()
    item.pop("source_account")
    connection = FakeConnection(rows=[(44,)])
    result = PostgresStore(
        lambda **_: connection,
        source_account_scope="configured-fixture-mailbox",
    ).execute({"records": [item]}, context())

    assert result.status == ResultState.SUCCESS
    assert connection.cursor_instance.executed[0][1][1] == "configured-fixture-mailbox"
    assert result.business_result["records"][0]["source_account"] == "configured-fixture-mailbox"


def test_validation_failure_does_not_open_database():
    called = False

    def connect(**_):
        nonlocal called
        called = True

    result = PostgresStore(connect).execute({"records": [record(amount="NaN")]}, context())

    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "INVALID_INPUT"
    assert result.errors[0].retryability == Retryability.NOT_RETRYABLE
    assert called is False


def test_dry_run_and_replay_preview_without_opening_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    for mode in (RunMode.DRY_RUN, RunMode.REPLAY):
        called = False

        def connect(**_):
            nonlocal called
            called = True

        run_context = context().model_copy(update={"mode": mode})
        result = PostgresStore(connect).execute({"records": [record()]}, run_context)

        assert result.status == ResultState.SUCCESS
        preview = result.business_result["records"][0]
        assert preview["record_id"] is None
        assert preview["duplicate"] is None
        assert preview["persistence_state"] == "PREVIEWED"
        assert called is False


def test_temporary_database_failure_is_retryable_and_safe_message():
    def connect(**_):
        raise psycopg.OperationalError("secret connection string and SQL params")

    result = PostgresStore(connect).execute({"records": [record()]}, context())

    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "DATABASE_TEMPORARY_FAILURE"
    assert result.errors[0].retryability == Retryability.RETRYABLE
    assert "secret" not in result.errors[0].message


def test_missing_database_url_is_explicit(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = PostgresStore().execute({"records": [record()]}, context())

    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "DATABASE_CONFIGURATION_MISSING"


def test_multiple_records_commit_in_one_connection_and_same_key_duplicates_resolve():
    connection = FakeConnection(rows=[(42,), None, (42, "fingerprint")])
    result = PostgresStore(lambda **_: connection).execute(
        {"records": [record(), record(record_key="second-reference")]}, context()
    )

    assert [item["duplicate"] for item in result.business_result["records"]] == [False, True]
    assert [item["record_id"] for item in result.business_result["records"]] == ["42", "42"]
    assert len(connection.cursor_instance.executed) == 3


def test_notification_outbox_creates_durable_intent_without_sending():
    payload = {"text": "Invoice INV-1001 is ready"}
    fingerprint = hashlib.sha256(
        f"#finance\n{json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True)}".encode()
    ).hexdigest()
    connection = FakeConnection(rows=[(71, "PENDING", "#finance", fingerprint)])
    result = PostgresStore(lambda **_: connection).enqueue_notification(
        "invoice:42:slack", "#finance", payload
    )

    assert result == {"id": "71", "created": True, "state": "PENDING"}
    sql, params = connection.cursor_instance.executed[0]
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert params[0:2] == ("invoice:42:slack", "#finance")


def test_notification_outbox_returns_existing_intent_on_duplicate_key():
    payload = {"text": "Invoice"}
    fingerprint = hashlib.sha256(
        f"#finance\n{json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True)}".encode()
    ).hexdigest()
    connection = FakeConnection(rows=[None, (71, "SENT", "#finance", fingerprint)])
    result = PostgresStore(lambda **_: connection).enqueue_notification(
        "invoice:42:slack", "#finance", payload
    )

    assert result == {"id": "71", "created": False, "state": "SENT"}


def test_notification_claim_is_conditional_and_only_one_worker_succeeds():
    connection = FakeConnection(rows=[(71, "SENDING", 1)])
    claimed = PostgresStore(lambda **_: connection).claim_notification("71")
    sql, params = connection.cursor_instance.executed[0]
    assert claimed is True
    assert "WHERE id = %s AND state = 'PENDING'" in sql
    assert params == (71,)

    already_claimed = PostgresStore(lambda **_: FakeConnection(rows=[None])).claim_notification("71")
    assert already_claimed is False


def test_sent_notification_payload_is_redacted_but_fingerprint_is_preserved():
    connection = FakeConnection(rows=[(71,)])
    store = PostgresStore(lambda **_: connection)
    assert store.redact_sent_notification("71") is True
    sql, params = connection.cursor_instance.executed[0]
    assert "SET payload = '{}'::jsonb" in sql
    assert "WHERE id = %s AND state = 'SENT'" in sql
    assert params == (71,)


def test_notification_payload_redaction_refuses_non_sent_row():
    connection = FakeConnection(rows=[None])
    assert PostgresStore(lambda **_: connection).redact_sent_notification("71") is False


def test_notification_key_reuse_with_changed_payload_is_rejected():
    connection = FakeConnection(rows=[None, (71, "PENDING", "#finance", "old-fingerprint")])
    try:
        PostgresStore(lambda **_: connection).enqueue_notification(
            "invoice:42:slack", "#finance", {"text": "changed"}
        )
    except NotificationOutboxError as error:
        assert error.code == "OUTBOX_IDEMPOTENCY_KEY_REUSED"
        assert error.retryability == Retryability.NOT_RETRYABLE
    else:
        raise AssertionError("the same idempotency key must not silently change intent")


def test_notification_payload_is_bounded_and_json_only():
    store = PostgresStore(lambda **_: FakeConnection(rows=[(1, "PENDING")]))
    for payload in ({"value": object()}, {"text": "x" * 16_385}):
        try:
            store.enqueue_notification("key", "destination", payload)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid or oversized outbox payload should be rejected")


def test_notification_update_validates_state_and_error_code():
    connection = FakeConnection(rows=[(71, "SENDING", 2)])
    store = PostgresStore(lambda **_: connection)
    result = store.update_notification(71, "sending")

    assert result == {"id": "71", "state": "SENDING", "attempt_count": 2}
    assert connection.cursor_instance.executed[0][1] == ("SENDING", None, "SENDING", 71)
    try:
        store.update_notification(71, "SENT", "provider timed out with PII")
    except ValueError:
        pass
    else:
        raise AssertionError("free-form provider messages must not be stored as error codes")


def test_notification_outbox_accepts_mocked_and_previewed_terminal_states():
    for state in ("MOCKED", "PREVIEWED"):
        connection = FakeConnection(rows=[(71, state, 0)])
        result = PostgresStore(lambda **_: connection).update_notification(71, state)
        assert result == {"id": "71", "state": state, "attempt_count": 0}


def test_notification_database_error_has_safe_retryable_diagnostic():
    def connect(**_):
        raise psycopg.OperationalError("credential and provider payload")

    try:
        PostgresStore(connect).enqueue_notification("key", "destination", {"text": "ok"})
    except NotificationOutboxError as error:
        assert error.code == "OUTBOX_TEMPORARY_FAILURE"
        assert error.retryability == Retryability.RETRYABLE
        assert "credential" not in str(error)
    else:
        raise AssertionError("database failure must be returned as a safe typed outbox error")
