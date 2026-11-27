from __future__ import annotations

from datetime import UTC, datetime

import pytest

from workflow_automation.contracts import ExecutionContext, ResultState, RunMode
from workflow_automation.modules.processing.source_poll_checkpoint import SourcePollCheckpoint


class _Store:
    def __init__(self, outcome=None):
        self.outcome = outcome or {"state": "ACQUIRED", "cursor_at": None,
                                   "lease_token": "b172e1db-6c36-4d12-a00c-601e4473e9f3",
                                   "lease_expires_at": "2026-10-03T05:00:00Z"}
        self.calls = []

    def acquire(self, **kwargs):
        self.calls.append(("acquire", kwargs))
        return self.outcome

    def commit(self, **kwargs):
        self.calls.append(("commit", kwargs))
        return self.outcome

    def release(self, **kwargs):
        self.calls.append(("release", kwargs))
        return self.outcome

    def renew(self, **kwargs):
        self.calls.append(("renew", kwargs))
        return self.outcome


def _context(mode=RunMode.MOCK):
    return ExecutionContext(workflow_id="test-poll-checkpoint", mode=mode)


def test_acquire_hashes_source_key_and_returns_database_lease():
    store = _Store()
    module = SourcePollCheckpoint(store=store)

    result = module.execute({"operation": "acquire", "checkpoint_key": "private-form-id",
                             "lease_owner_id": "execution-123"}, _context())

    assert result.status == ResultState.SUCCESS
    assert result.business_result["state"] == "ACQUIRED"
    assert result.business_result["lease_token"] == "b172e1db-6c36-4d12-a00c-601e4473e9f3"
    assert "private-form-id" not in str(store.calls)
    assert store.calls[0][1]["lease_seconds"] == 900
    assert len(store.calls[0][1]["key_hash"]) == 64


def test_busy_acquire_is_successful_no_work_result():
    store = _Store({"state": "BUSY", "cursor_at": "2026-10-03T04:00:00Z"})
    result = SourcePollCheckpoint(store=store).execute(
        {"operation": "acquire", "checkpoint_key": "form", "lease_owner_id": "execution-2"}, _context()
    )
    assert result.status == ResultState.SUCCESS
    assert result.business_result["state"] == "BUSY"


def test_commit_parses_utc_cursor_and_hashes_checkpoint_identity():
    store = _Store({"state": "COMMITTED", "cursor_at": "2026-10-03T04:10:00Z"})
    result = SourcePollCheckpoint(store=store).execute(
        {"operation": "commit", "checkpoint_key": "private-form-id",
         "lease_owner_id": "execution-123", "lease_token": "b172e1db-6c36-4d12-a00c-601e4473e9f3",
         "cursor_at": "2026-10-03T04:10:00Z"}, _context()
    )
    assert result.status == ResultState.SUCCESS
    assert store.calls[0][1]["cursor_at"] == datetime(2026, 10, 3, 4, 10, tzinfo=UTC)
    assert len(store.calls[0][1]["key_hash"]) == 64


def test_stale_lease_commit_fails_closed():
    store = _Store({"state": "LEASE_LOST"})
    result = SourcePollCheckpoint(store=store).execute(
        {"operation": "commit", "checkpoint_key": "form", "lease_owner_id": "old-run",
         "lease_token": "b172e1db-6c36-4d12-a00c-601e4473e9f3", "cursor_at": "2026-10-03T04:10:00Z"},
        _context(),
    )
    assert result.status == ResultState.REVIEW_REQUIRED
    assert result.business_result["state"] == "LEASE_LOST"
    assert result.errors[0].retryability.value == "NOT_RETRYABLE"


def test_release_uses_hashed_checkpoint_key_and_lease_token():
    store = _Store({"state": "RELEASED", "cursor_at": None})
    result = SourcePollCheckpoint(store=store).execute(
        {"operation": "release", "checkpoint_key": "private-form-id", "lease_owner_id": "run-1",
         "lease_token": "b172e1db-6c36-4d12-a00c-601e4473e9f3"}, _context(),
    )
    assert result.status == ResultState.SUCCESS
    assert result.business_result["state"] == "RELEASED"
    assert len(store.calls[0][1]["key_hash"]) == 64


def test_renewal_uses_same_fenced_lease_and_configured_expiry():
    store = _Store({"state": "RENEWED", "lease_expires_at": "2026-10-03T05:00:00Z"})
    result = SourcePollCheckpoint(store=store, lease_seconds=600).execute(
        {"operation": "renew", "checkpoint_key": "form", "lease_owner_id": "run-1",
         "lease_token": "b172e1db-6c36-4d12-a00c-601e4473e9f3"}, _context(),
    )
    assert result.status == ResultState.SUCCESS
    assert result.business_result["state"] == "RENEWED"
    assert store.calls[0][1]["lease_seconds"] == 600


def test_dry_run_never_claims_or_commits_checkpoint():
    store = _Store()
    result = SourcePollCheckpoint(store=store).execute(
        {"operation": "acquire", "checkpoint_key": "form", "lease_owner_id": "preview"},
        _context(RunMode.DRY_RUN),
    )
    assert result.business_result["state"] == "PREVIEWED"
    assert store.calls == []


@pytest.mark.parametrize("payload", [
    {"operation": [], "checkpoint_key": "form", "lease_owner_id": "run"},
    {"operation": "acquire", "checkpoint_key": "", "lease_owner_id": "run"},
    {"operation": "acquire", "checkpoint_key": "form", "lease_owner_id": "bad owner"},
    {"operation": "commit", "checkpoint_key": "form", "lease_owner_id": "run",
     "lease_token": "not-a-uuid", "cursor_at": "2026-10-03T04:10:00Z"},
    {"operation": "commit", "checkpoint_key": "form", "lease_owner_id": "run",
     "lease_token": "b172e1db-6c36-4d12-a00c-601e4473e9f3", "cursor_at": "2026-10-03T04:10:00-07:00"},
    {"operation": "renew", "checkpoint_key": "form", "lease_owner_id": "run", "lease_token": "not-a-uuid"},
])
def test_invalid_payload_is_rejected_without_store_call(payload):
    store = _Store()
    result = SourcePollCheckpoint(store=store).execute(payload, _context())
    assert result.status == ResultState.FAILURE
    assert store.calls == []
