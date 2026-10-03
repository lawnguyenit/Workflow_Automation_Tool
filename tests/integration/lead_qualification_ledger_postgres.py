"""Synthetic durable qualification ledger checks against local Compose PostgreSQL.

Run from the host with:
  Get-Content -Raw tests/integration/lead_qualification_ledger_postgres.py | docker compose exec -T api python -

The operation callback below is local synthetic code; it does not contact a model
provider or use user/client data.
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urlparse

import psycopg

from workflow_automation.infrastructure.postgres_lead_qualification_ledger import (
    PostgresLeadQualificationLedger,
    QualificationBudgetExhausted,
    QualificationInProgress,
    QualificationInputConflict,
    QualificationReconciliationRequired,
)


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

ledger = PostgresLeadQualificationLedger(dsn=database_url)
marker = uuid.uuid4().hex
model = f"synthetic-ledger-model-{marker}"
model_key = hashlib.sha256(model.encode()).hexdigest()
prompt_version = "synthetic_prompt_v1"
criteria_hash = hashlib.sha256(b"synthetic criteria").hexdigest()
base_input_hash = hashlib.sha256(b"synthetic input").hexdigest()
results = {
    "classification": "qualified",
    "summary": "Synthetic qualification result.",
    "confidence": 0.9,
    "missing_information": [],
    "reasons": [],
}
event_ids = [hashlib.sha256(f"{marker}:{index}".encode()).hexdigest() for index in range(4)]
provider_calls = 0


def run(event_key: str, operation, *, input_fingerprint: str = base_input_hash,
        limit: int = 10) -> dict:
    return ledger.run_once(
        event_key=event_key,
        input_fingerprint=input_fingerprint,
        criteria_fingerprint=criteria_hash,
        provider_model=model,
        prompt_version=prompt_version,
        max_calls_per_day=limit,
        operation=operation,
    )


def counted_result():
    global provider_calls
    provider_calls += 1
    return results


try:
    first = run(event_ids[0], counted_result)
    retry = run(event_ids[0], counted_result)
    assert first == retry == results
    assert provider_calls == 1
    try:
        run(event_ids[0], counted_result,
            input_fingerprint=hashlib.sha256(b"changed synthetic input").hexdigest())
        raise AssertionError("changed source payload must not trigger another model call")
    except QualificationInputConflict:
        pass

    def ambiguous_operation():
        raise RuntimeError("synthetic provider result unknown")

    try:
        run(event_ids[1], ambiguous_operation)
        raise AssertionError("synthetic uncertain result should be held")
    except RuntimeError as exc:
        assert "synthetic provider" in str(exc)
    try:
        run(event_ids[1], counted_result)
        raise AssertionError("unknown call must not be retried automatically")
    except QualificationReconciliationRequired:
        pass

    started = threading.Event()
    release = threading.Event()
    concurrent_calls = []

    def slow_operation():
        concurrent_calls.append(True)
        started.set()
        if not release.wait(timeout=5):
            raise RuntimeError("synthetic concurrency check timed out")
        return results

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(run, event_ids[2], slow_operation)
        assert started.wait(timeout=5)
        try:
            run(event_ids[2], counted_result)
            raise AssertionError("overlapping event claim should be held")
        except QualificationInProgress:
            pass
        release.set()
        assert first_future.result(timeout=5) == results
    assert len(concurrent_calls) == 1

    today = datetime.now(UTC).date()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO workflow_lead_qualification_daily_budget
                       (call_day_utc, model_key, attempted_calls)
                   VALUES (%s, %s, 1)
                   ON CONFLICT (call_day_utc, model_key)
                   DO UPDATE SET attempted_calls = 1, updated_at = now()""",
                (today, model_key),
            )
    try:
        run(event_ids[3], counted_result, limit=1)
        raise AssertionError("the daily provider-call cap must block new requests")
    except QualificationBudgetExhausted:
        pass
finally:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM workflow_lead_qualification_calls WHERE event_key = ANY(%s)",
                (event_ids,),
            )
            cursor.execute(
                "DELETE FROM workflow_lead_qualification_daily_budget WHERE model_key = %s",
                (model_key,),
            )

print({
    "same_event_retry": "CACHED_NO_PROVIDER_CALL",
    "changed_payload": "HELD_FOR_REVIEW",
    "ambiguous_call_retry": "RECONCILIATION_REQUIRED",
    "overlapping_same_event": "ONE_PROVIDER_CALL",
    "daily_call_limit": "ENFORCED",
    "synthetic_rows_removed": True,
})
