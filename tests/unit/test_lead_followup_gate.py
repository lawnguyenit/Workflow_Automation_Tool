from __future__ import annotations

import hashlib
import hmac

from workflow_automation.contracts import ExecutionContext, ResultState, RunMode
from workflow_automation.infrastructure.postgres_lead_followup_state import (
    FollowupEventConflict,
    FollowupStateUnavailable,
)
from workflow_automation.modules.processing.lead_followup_gate import LeadFollowupGate

TEST_IDENTITY_KEY = b"unit-test-key-that-is-at-least-32-bytes"


class FakeStore:
    def __init__(self):
        self.calls = []
        self.response = {"state": "ACTIVE", "can_send": True, "reason": "ELIGIBLE"}
        self.error = None

    def check(self, **kwargs):
        self.calls.append(("check", kwargs))
        if self.error:
            raise self.error
        return self.response

    def apply_event(self, **kwargs):
        self.calls.append(("apply_event", kwargs))
        if self.error:
            raise self.error
        return self.response


def _check_payload():
    return {"operation": "check", "subject_scope": "test-location", "subject_id": "contact-123"}


def _event_payload(event_type="REPLIED"):
    return {
        "operation": "record_event",
        "subject_scope": "test-location",
        "subject_id": "contact-123",
        "source_system": "test-events",
        "source_account": "test-account",
        "source_event_id": "event-123",
        "event_type": event_type,
    }


def _gate(store):
    return LeadFollowupGate(store=store, identity_key=TEST_IDENTITY_KEY)


def _identity(*parts):
    material = "\0".join(parts).encode("utf-8")
    return hmac.new(TEST_IDENTITY_KEY, material, hashlib.sha256).hexdigest()


def test_dry_run_never_authorizes_send_or_reads_persistent_state():
    store = FakeStore()
    result = _gate(store).execute(_check_payload(),
        ExecutionContext(workflow_id="test", mode=RunMode.DRY_RUN))
    assert result.status == ResultState.REVIEW_REQUIRED
    assert result.business_result["can_send"] is False
    assert store.calls == []


def test_check_returns_only_gate_decision_and_hashes_subject_before_store():
    store = FakeStore()
    result = _gate(store).execute(_check_payload(),
        ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert result.status == ResultState.SUCCESS
    assert result.business_result["can_send"] is True
    assert store.calls == [("check", {"subject_key": _identity("subject", "test-location", "contact-123")})]
    assert "contact-123" not in result.model_dump_json()


def test_missing_or_short_hmac_key_fails_closed_before_database_access():
    store = FakeStore()
    result = LeadFollowupGate(store=store, identity_key="short").execute(
        _check_payload(), ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "FOLLOWUP_IDENTITY_KEY_NOT_CONFIGURED"
    assert result.business_result["can_send"] is False
    assert store.calls == []


def test_blocked_state_is_review_required_and_never_sendable():
    store = FakeStore()
    store.response = {"state": "STOPPED_OPTED_OUT", "can_send": False, "reason": "OPTED_OUT"}
    result = _gate(store).execute(_check_payload(),
        ExecutionContext(workflow_id="test", mode=RunMode.MOCK))
    assert result.status == ResultState.REVIEW_REQUIRED
    assert result.business_result["can_send"] is False
    assert result.business_result["reason"] == "OPTED_OUT"


def test_record_event_hashes_subject_event_and_payload_without_echoing_identifiers():
    store = FakeStore()
    payload = _event_payload()
    result = _gate(store).execute(payload,
        ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    call, args = store.calls[0]
    assert call == "apply_event"
    assert args["subject_key"] == _identity("subject", "test-location", "contact-123")
    assert args["event_key"] == _identity("event", "test-events", "test-account", "event-123")
    assert args["event_type"] == "REPLIED"
    assert result.status == ResultState.SUCCESS
    assert "contact-123" not in result.model_dump_json()
    assert "event-123" not in result.model_dump_json()


def test_manual_resume_is_bound_to_exact_pause_event_identity():
    store = FakeStore()
    payload = _event_payload("MANUAL_RESUME")
    payload["expected_pause_event"] = {
        "source_system": "operator",
        "source_account": "local",
        "source_event_id": "pause-456",
    }
    _gate(store).execute(payload,
        ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    args = store.calls[0][1]
    assert args["expected_pause_key"] == _identity("event", "operator", "local", "pause-456")


def test_stale_manual_resume_stays_blocked_for_review():
    store = FakeStore()
    store.response = {"state": "PAUSED", "can_send": False,
                      "reason": "PAUSED", "event_outcome": "STALE_RESUME_REVIEW", "duplicate": False}
    result = _gate(store).execute(_event_payload("MANUAL_RESUME") | {
        "expected_pause_event": {"source_system": "ops", "source_account": "local",
                                 "source_event_id": "old-pause"}},
        ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert result.status == ResultState.REVIEW_REQUIRED
    assert result.errors[0].code == "STALE_MANUAL_RESUME"
    assert result.business_result["can_send"] is False


def test_persisted_stop_event_is_successfully_recorded_but_never_sendable():
    store = FakeStore()
    store.response = {"state": "STOPPED_REPLIED", "can_send": False,
                      "reason": "REPLIED", "event_outcome": "REPLIED", "duplicate": False}
    result = _gate(store).execute(_event_payload("REPLIED"),
        ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert result.status == ResultState.SUCCESS
    assert result.business_result["can_send"] is False
    assert result.business_result["state"] == "STOPPED_REPLIED"


def test_conflicts_and_unavailable_state_fail_closed():
    store = FakeStore()
    gate = _gate(store)
    store.error = FollowupEventConflict("changed event")
    conflict = gate.execute(_event_payload(), ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert conflict.status == ResultState.REVIEW_REQUIRED
    assert conflict.business_result["can_send"] is False
    assert conflict.errors[0].code == "SUPPRESSION_EVENT_CONFLICT"
    store.error = FollowupStateUnavailable("database down")
    unavailable = gate.execute(_check_payload(), ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert unavailable.status == ResultState.REVIEW_REQUIRED
    assert unavailable.business_result["can_send"] is False


def test_input_contract_rejects_unknown_fields_and_unhashable_event_type():
    gate = _gate(FakeStore())
    bad_extra = _check_payload() | {"email": "person@example.com"}
    assert gate.execute(bad_extra, ExecutionContext(workflow_id="test")).errors[0].code == "INVALID_FOLLOWUP_GATE_INPUT"
    bad_type = _event_payload() | {"event_type": []}
    assert gate.execute(bad_type, ExecutionContext(workflow_id="test")).errors[0].code == "INVALID_FOLLOWUP_GATE_INPUT"
