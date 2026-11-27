"""Synthetic suppression-state checks against local Compose PostgreSQL only.

Run from the host with:
  Get-Content -Raw tests/integration/lead_followup_suppression_postgres.py | docker compose exec -T api python -

No provider, CRM, email, Google, or client service is contacted.
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import psycopg

from workflow_automation.infrastructure.postgres_lead_followup_state import (
    FollowupEventConflict,
    FollowupStateUnavailable,
    PostgresLeadFollowupState,
)


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

store = PostgresLeadFollowupState(dsn=database_url)
marker = uuid.uuid4().hex
digest = lambda value: hashlib.sha256(f"{marker}:{value}".encode()).hexdigest()
subjects = {key: digest(f"subject:{key}") for key in (
    "main", "reply", "booking", "optout", "race-a", "race-b"
)}
events = {key: digest(f"event:{key}") for key in (
    "enroll", "pause", "stale-resume", "resume", "reply", "booking", "optout",
    "race-reply", "race-enroll", "shared-event"
)}
fingerprint = digest("fingerprint")


def apply(subject: str, event: str, kind: str, payload: str = "fingerprint", pause: str | None = None):
    return store.apply_event(
        subject_key=subjects[subject], event_key=events[event],
        payload_fingerprint=digest(payload), event_type=kind,
        expected_pause_key=events[pause] if pause else None,
    )


def assert_terminal(subject: str, kind: str, event: str) -> None:
    stopped = apply(subject, event, kind)
    assert stopped["can_send"] is False
    assert stopped["state"] == f"STOPPED_{kind}"
    reenroll_key = f"{event}-reenroll"
    events[reenroll_key] = digest(f"{event}:reenroll")
    re_enrolled = store.apply_event(
        subject_key=subjects[subject], event_key=events[reenroll_key],
        payload_fingerprint=fingerprint, event_type="FOLLOWUP_ENROLLED",
    )
    assert re_enrolled["state"] == f"STOPPED_{kind}"
    assert store.check(subject_key=subjects[subject])["can_send"] is False


try:
    assert store.check(subject_key=subjects["main"])["state"] == "NOT_ENROLLED"
    assert apply("main", "enroll", "FOLLOWUP_ENROLLED")["can_send"] is True
    retried = apply("main", "enroll", "FOLLOWUP_ENROLLED")
    assert retried["duplicate"] is True and retried["state"] == "ACTIVE"
    try:
        apply("main", "enroll", "REPLIED", payload="changed")
        raise AssertionError("same event identity with changed content must conflict")
    except FollowupEventConflict:
        pass

    assert apply("main", "pause", "MANUAL_PAUSE")["state"] == "PAUSED"
    stale = apply("main", "stale-resume", "MANUAL_RESUME", pause="enroll")
    assert stale["state"] == "PAUSED" and stale["event_outcome"] == "STALE_RESUME_REVIEW"
    assert apply("main", "resume", "MANUAL_RESUME", pause="pause")["can_send"] is True

    assert_terminal("reply", "REPLIED", "reply")
    assert_terminal("booking", "BOOKED", "booking")
    assert_terminal("optout", "OPTED_OUT", "optout")

    # Overlapping stop/enroll events serialize by subject; stop always wins.
    start = threading.Barrier(2)

    def race(kind: str, event: str):
        start.wait(timeout=5)
        return apply("race-a", event, kind)

    with ThreadPoolExecutor(max_workers=2) as pool:
        stop_future = pool.submit(race, "REPLIED", "race-reply")
        enroll_future = pool.submit(race, "FOLLOWUP_ENROLLED", "race-enroll")
        stop_future.result(timeout=10)
        enroll_future.result(timeout=10)
    assert store.check(subject_key=subjects["race-a"])["state"] == "STOPPED_REPLIED"

    # A saturated/stuck subject lock must fail closed within the configured bound.
    try:
        with psycopg.connect(database_url) as blocker:
            with blocker.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (subjects["main"],),
                )
            with ThreadPoolExecutor(max_workers=1) as pool:
                blocked_check = pool.submit(store.check, subject_key=subjects["main"])
                try:
                    blocked_check.result(timeout=6)
                    raise AssertionError("a lock timeout must fail closed")
                except FollowupStateUnavailable:
                    pass
    except TimeoutError as exc:
        raise AssertionError("PostgreSQL gate lock exceeded the six-second test bound") from exc

    # A globally reused source-event identity across different subjects is detected.
    barrier = threading.Barrier(2)

    def shared_event(subject: str):
        barrier.wait(timeout=5)
        return store.apply_event(
            subject_key=subjects[subject], event_key=events["shared-event"],
            payload_fingerprint=digest(f"shared:{subject}"), event_type="FOLLOWUP_ENROLLED",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(shared_event, "race-a"), pool.submit(shared_event, "race-b")]
        outcomes = []
        conflicts = 0
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except FollowupEventConflict:
                conflicts += 1
    assert len(outcomes) == 1 and conflicts == 1
finally:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM workflow_lead_followup_events WHERE event_key = ANY(%s)",
                           (list(events.values()),))
            cursor.execute("DELETE FROM workflow_lead_followup_control WHERE subject_key = ANY(%s)",
                           (list(subjects.values()),))

print({
    "enrollment_and_check": "PASS",
    "event_retry_and_conflict": "PASS",
    "pause_and_exact_resume": "PASS",
    "stale_resume": "REVIEW_AND_BLOCKED",
    "reply_booking_opt_out": "TERMINAL_AND_BLOCKED",
    "concurrent_stop_enroll": "STOP_WINS",
    "database_lock_timeout": "FAIL_CLOSED",
    "global_event_identity_race": "ONE_ACCEPTED_ONE_CONFLICT",
    "synthetic_rows_removed": True,
})
