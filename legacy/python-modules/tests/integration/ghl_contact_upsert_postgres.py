"""Synthetic GHL contact upserts through the real Compose PostgreSQL event ledger.

Run from the host with:
  Get-Content -Raw tests/integration/ghl_contact_upsert_postgres.py | docker compose exec -T api python -

The HTTP transport is always synthetic. No HighLevel account or contact is used.
"""

from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
import psycopg

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.infrastructure.postgres_ghl_contact_upsert_ledger import PostgresGHLContactUpsertLedger
from workflow_automation.modules.crm.ghl_upsert_contact import GHLUpsertContact


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

location_id = "synthetic-contact-upsert-location"
event_id = "synthetic:forms:account:response-overlap"
unknown_event_id = "synthetic:forms:account:response-unknown"
success_event_id = "synthetic:forms:account:response-success-cache"
effect_keys = [
    hashlib.sha256(("ghl-v3-contact-upsert\0" + location_id + "\0" + key).encode()).hexdigest()
    for key in (event_id, unknown_event_id, success_event_id)
]
started = threading.Event()
release = threading.Event()
requests: list[str] = []


def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request.url.path)
    body = request.read().decode()
    if "synthetic-unknown@example.invalid" in body:
        raise httpx.ReadTimeout("synthetic unknown contact write", request=request)
    started.set()
    if not release.wait(timeout=5):
        raise httpx.ReadTimeout("synthetic integration timeout", request=request)
    return httpx.Response(200, json={"new": True, "contact": {"id": "synthetic-contact-id"}})


client = httpx.Client(transport=httpx.MockTransport(handler))
context = ExecutionContext(workflow_id="synthetic_ghl_contact_upsert", mode=RunMode.REAL)
module = GHLUpsertContact(location_id=location_id, bearer_token="synthetic-token", http_client=client,
                          effect_ledger=PostgresGHLContactUpsertLedger(dsn=database_url))


def upsert(event_key: str, email: str) -> dict:
    return module.execute({"idempotency_key": event_key,
                           "contact": {"email": email, "name": "Synthetic Lead"}}, context).model_dump()


try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(upsert, event_id, "synthetic-overlap@example.invalid")
        assert started.wait(timeout=5)
        overlapping = upsert(event_id, "synthetic-overlap@example.invalid")
        release.set()
        first = first_future.result(timeout=5)
    assert first["business_result"]["state"] == "UPSERTED"
    assert overlapping["errors"][0]["code"] == "CONTACT_WRITE_IN_PROGRESS"
    assert requests.count("/contacts/upsert") == 1

    unknown = upsert(unknown_event_id, "synthetic-unknown@example.invalid")
    blocked_retry = upsert(unknown_event_id, "synthetic-unknown@example.invalid")
    assert unknown["errors"][0]["code"] == "REMOTE_OUTCOME_UNKNOWN"
    assert blocked_retry["errors"][0]["code"] == "CONTACT_RECONCILIATION_REQUIRED"
    assert requests.count("/contacts/upsert") == 2

    first_success = upsert(success_event_id, "synthetic-success-cache@example.invalid")
    cached_success = upsert(success_event_id, "synthetic-success-cache@example.invalid")
    changed_payload = upsert(success_event_id, "synthetic-changed-cache@example.invalid")
    assert first_success["business_result"]["side_effect"] is True
    assert cached_success["business_result"]["cached"] is True
    assert cached_success["business_result"]["side_effect"] is False
    assert changed_payload["errors"][0]["code"] == "CONTACT_EVENT_PAYLOAD_CONFLICT"
    assert requests.count("/contacts/upsert") == 3
finally:
    client.close()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM workflow_ghl_contact_upserts WHERE event_key = ANY(%s)",
                (effect_keys,),
            )
            cursor.execute("SELECT count(*) FROM workflow_ghl_contact_upserts WHERE event_key = ANY(%s)",
                           (effect_keys,))
            assert cursor.fetchone()[0] == 0

print({
    "synthetic_http_transport": True,
    "same_event_overlapping_calls": "ONE_PROVIDER_REQUEST",
    "confirmed_success_replay": "CACHED_WITHOUT_PROVIDER_REQUEST",
    "changed_payload_replay": "HELD_FOR_REVIEW",
    "unknown_write_retry": "BLOCKED_FOR_RECONCILIATION",
    "reconciliation_rows_removed": True,
})
