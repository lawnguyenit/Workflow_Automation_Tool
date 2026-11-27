"""Synthetic lease/checkpoint checks against local Compose PostgreSQL only.

Run from the host with:
  Get-Content -Raw tests/integration/source_poll_checkpoint_postgres.py | docker compose exec -T api python -

No Google, CRM, model, email, or client service is contacted.
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import psycopg

from workflow_automation.infrastructure.postgres_source_poll_checkpoint import PostgresSourcePollCheckpoint


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

key_hash = hashlib.sha256(f"synthetic-poll-checkpoint:{uuid.uuid4()}".encode()).hexdigest()
race_key_hash = hashlib.sha256(f"synthetic-poll-checkpoint-race:{uuid.uuid4()}".encode()).hexdigest()
store = PostgresSourcePollCheckpoint(dsn=database_url)
initial_cursor = datetime(2026, 10, 3, 4, 0, tzinfo=UTC)
advanced_cursor = initial_cursor + timedelta(minutes=5)

try:
    first = store.acquire(key_hash=key_hash, owner_id="synthetic-run-1", lease_seconds=900)
    assert first["state"] == "ACQUIRED" and first["cursor_at"] is None

    retry_same_run = store.acquire(key_hash=key_hash, owner_id="synthetic-run-1", lease_seconds=900)
    assert retry_same_run["state"] == "ACQUIRED"
    assert retry_same_run["lease_token"] == first["lease_token"]

    renewed = store.renew(key_hash=key_hash, owner_id="synthetic-run-1",
                          lease_token=uuid.UUID(first["lease_token"]), lease_seconds=900)
    assert renewed["state"] == "RENEWED"

    overlap = store.acquire(key_hash=key_hash, owner_id="synthetic-run-2", lease_seconds=900)
    assert overlap["state"] == "BUSY"

    committed = store.commit(key_hash=key_hash, owner_id="synthetic-run-1",
                             lease_token=uuid.UUID(first["lease_token"]), cursor_at=advanced_cursor)
    assert committed == {"state": "COMMITTED", "cursor_at": "2026-10-03T04:05:00.000Z"}
    repeated_commit = store.commit(key_hash=key_hash, owner_id="synthetic-run-1",
                                   lease_token=uuid.UUID(first["lease_token"]), cursor_at=advanced_cursor)
    assert repeated_commit == committed

    second = store.acquire(key_hash=key_hash, owner_id="synthetic-run-2", lease_seconds=900)
    assert second["state"] == "ACQUIRED" and second["cursor_at"] == committed["cursor_at"]

    # The checkpoint is monotonic even if a caller presents an earlier boundary.
    monotonic = store.commit(key_hash=key_hash, owner_id="synthetic-run-2",
                             lease_token=uuid.UUID(second["lease_token"]), cursor_at=initial_cursor)
    assert monotonic["cursor_at"] == committed["cursor_at"]

    third = store.acquire(key_hash=key_hash, owner_id="synthetic-run-3", lease_seconds=900)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE workflow_source_poll_checkpoints SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE key_hash = %s",
                (key_hash,),
            )
    fourth = store.acquire(key_hash=key_hash, owner_id="synthetic-run-4", lease_seconds=900)
    assert fourth["state"] == "ACQUIRED"

    stale = store.commit(key_hash=key_hash, owner_id="synthetic-run-3",
                         lease_token=uuid.UUID(third["lease_token"]), cursor_at=advanced_cursor + timedelta(minutes=5))
    assert stale["state"] == "LEASE_LOST"

    released = store.release(key_hash=key_hash, owner_id="synthetic-run-4",
                             lease_token=uuid.UUID(fourth["lease_token"]))
    assert released["state"] == "RELEASED"
    repeated_release = store.release(key_hash=key_hash, owner_id="synthetic-run-4",
                                     lease_token=uuid.UUID(fourth["lease_token"]))
    assert repeated_release == released

    barrier = threading.Barrier(2)

    def race_acquire(owner: str):
        barrier.wait(timeout=5)
        return store.acquire(key_hash=race_key_hash, owner_id=owner, lease_seconds=900)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(race_acquire, "race-run-a"), pool.submit(race_acquire, "race-run-b")]
        race_results = [future.result(timeout=10)["state"] for future in futures]
    assert sorted(race_results) == ["ACQUIRED", "BUSY"]
finally:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM workflow_source_poll_checkpoints WHERE key_hash = ANY(%s)",
                           ([key_hash, race_key_hash],))

print({
    "same_run_acquire_retry": "SAME_LEASE_TOKEN",
    "lease_renewal": "RENEWED",
    "overlapping_run": "BUSY",
    "accepted_commit": "MONOTONIC_CURSOR",
    "commit_retry": "IDEMPOTENT",
    "expired_lease_reclaim": "ACQUIRED",
    "stale_run_commit": "LEASE_LOST",
    "explicit_release": "RELEASED",
    "simultaneous_acquire": "ONE_ACQUIRED_ONE_BUSY",
    "synthetic_rows_removed": True,
})
