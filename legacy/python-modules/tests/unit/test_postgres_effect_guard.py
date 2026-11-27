import hashlib

import pytest
import psycopg

from workflow_automation.infrastructure.postgres_effect_guard import (
    EffectGuardBusy,
    EffectGuardNotConfigured,
    EffectReconciliationPending,
    PostgresExternalEffectGuard,
)


class _Cursor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return self.responses.pop(0)


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def _key():
    return hashlib.sha256(b"synthetic-key").hexdigest()


def _guard(responses):
    cursor = _Cursor(responses)
    guard = PostgresExternalEffectGuard(
        dsn="postgresql://test",
        connection_factory=lambda _dsn, autocommit: _Connection(cursor) if autocommit else None,
    )
    return guard, cursor


def test_acquires_and_releases_advisory_lock_around_external_operation():
    guard, cursor = _guard([(True,), None, (_key(),), (True,)])
    calls = []
    result = guard.run(effect_key=_key(), effect_type="synthetic.effect", correlation_id="run-1",
                       operation=lambda: calls.append("external") or "done",
                       uncertain_error_code=lambda _result: None)
    assert result == "done"
    assert calls == ["external"]
    assert "pg_try_advisory_lock" in cursor.calls[0][0]
    assert "pg_advisory_unlock" in cursor.calls[-1][0]


def test_busy_lock_or_pending_reconciliation_never_calls_external_effect():
    guard, _cursor = _guard([(False,)])
    calls = []
    with pytest.raises(EffectGuardBusy):
        guard.run(effect_key=_key(), effect_type="synthetic.effect", correlation_id="run-1",
                  operation=lambda: calls.append(True), uncertain_error_code=lambda _result: None)
    assert calls == []

    guard, cursor = _guard([(True,), ("pending",), (True,)])
    with pytest.raises(EffectReconciliationPending):
        guard.run(effect_key=_key(), effect_type="synthetic.effect", correlation_id="run-2",
                  operation=lambda: calls.append(True), uncertain_error_code=lambda _result: None)
    assert calls == []
    assert "pg_advisory_unlock" in cursor.calls[-1][0]


def test_uncertain_provider_write_is_persisted_as_hash_only_reconciliation():
    guard, cursor = _guard([(True,), None, (_key(),), (True,)])
    result = guard.run(effect_key=_key(), effect_type="ghl.opportunity", correlation_id="corr-1",
                       operation=lambda: {"state": "unknown", "email": "must-not-be-stored"},
                       uncertain_error_code=lambda _result: "REMOTE_OUTCOME_UNKNOWN")
    assert result["state"] == "unknown"
    claim = next(call for call in cursor.calls if "INSERT INTO workflow_external_effect_reconciliations" in call[0])
    update = next(call for call in cursor.calls if "UPDATE workflow_external_effect_reconciliations" in call[0])
    assert claim[1] == (_key(), "ghl.opportunity", "corr-1")
    assert update[1] == ("REMOTE_OUTCOME_UNKNOWN", _key())
    assert "must-not-be-stored" not in str(cursor.calls)


def test_unexpected_failure_after_claim_keeps_effect_blocked_for_reconciliation():
    guard, cursor = _guard([(True,), None, (_key(),), (True,)])
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        guard.run(effect_key=_key(), effect_type="synthetic.effect", correlation_id="run-1",
                  operation=lambda: (_ for _ in ()).throw(RuntimeError("synthetic interruption")),
                  uncertain_error_code=lambda _result: None)
    queries = [query for query, _params in cursor.calls]
    assert any("INSERT INTO workflow_external_effect_reconciliations" in query for query in queries)
    assert not any("DELETE FROM workflow_external_effect_reconciliations" in query for query in queries)


def test_requires_hashed_keys_and_database_configuration():
    guard = PostgresExternalEffectGuard(dsn=None)
    with pytest.raises(EffectGuardNotConfigured):
        guard.run(effect_key="bad", effect_type="synthetic.effect", correlation_id="run",
                  operation=lambda: True, uncertain_error_code=lambda _result: None)
    guard = PostgresExternalEffectGuard(dsn="")
    with pytest.raises(EffectGuardNotConfigured):
        guard.run(effect_key=_key(), effect_type="synthetic.effect", correlation_id="run",
                  operation=lambda: True, uncertain_error_code=lambda _result: None)
