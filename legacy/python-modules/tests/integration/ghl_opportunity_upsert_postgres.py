"""Synthetic GHL opportunity upserts through the real Compose PostgreSQL event and entity guards.

Run from the host with:
  Get-Content -Raw tests/integration/ghl_opportunity_upsert_postgres.py | docker compose exec -T api python -

The HTTP transport is always synthetic. No HighLevel account or opportunity is used.
"""

from __future__ import annotations

import os
import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
import psycopg

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.infrastructure.postgres_effect_guard import PostgresExternalEffectGuard
from workflow_automation.modules.crm.ghl_upsert_opportunity import GHLUpsertOpportunity


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

location_id = "synthetic-opportunity-location"
parallel = {"contactId": "synthetic-contact-overlap", "pipelineId": "synthetic-pipeline-overlap"}
unknown = {"contactId": "synthetic-contact-unknown", "pipelineId": "synthetic-pipeline-unknown"}
success = {"contactId": "synthetic-contact-success", "pipelineId": "synthetic-pipeline-success"}
same_event = {"contactId": "synthetic-contact-same-event", "pipelineId": "synthetic-pipeline-same-event"}
effect_keys = [GHLUpsertOpportunity._effect_key(location_id, item["contactId"], item["pipelineId"])
               for item in (parallel, unknown, success, same_event)]
event_ids = {
    "overlap": "synthetic:forms:account:opportunity-overlap",
    "overlap-other-event": "synthetic:forms:account:opportunity-overlap-other-event",
    "same-event": "synthetic:forms:account:opportunity-same-event",
    "unknown": "synthetic:forms:account:opportunity-unknown",
    "success": "synthetic:forms:account:opportunity-success",
}
event_keys = [hashlib.sha256(("ghl-v3-opportunity-upsert\0" + location_id + "\0" + key).encode()).hexdigest()
              for key in event_ids.values()]
started = threading.Event()
release = threading.Event()
requests: list[str] = []


def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request.method + " " + request.url.path)
    if request.method == "GET":
        if "unknown" in request.url.params["contactId"]:
            return httpx.Response(200, json={"opportunities": [], "meta": {"total": 0}})
        started.set()
        if not release.wait(timeout=5):
            raise httpx.ReadTimeout("synthetic integration timeout", request=request)
        return httpx.Response(200, json={"opportunities": [], "meta": {"total": 0}})
    if "unknown" in json.loads(request.read()).get("contactId", ""):
        raise httpx.ReadTimeout("synthetic unknown opportunity write", request=request)
    return httpx.Response(201, json={"opportunity": {"id": "synthetic-opportunity-id"}})


client = httpx.Client(transport=httpx.MockTransport(handler))
context = ExecutionContext(workflow_id="synthetic_ghl_opportunity_upsert", mode=RunMode.REAL)
module = GHLUpsertOpportunity(location_id=location_id, bearer_token="synthetic-token", http_client=client,
                              effect_guard=PostgresExternalEffectGuard(dsn=database_url))


def upsert(ids: dict[str, str], event_id: str, *, name: str = "Synthetic") -> dict:
    return module.execute({"idempotency_key": event_id,
                           "opportunity": {**ids, "pipelineStageId": "synthetic-stage", "name": name,
                                           "status": "open"}}, context).model_dump()


try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(upsert, parallel, event_ids["overlap"])
        assert started.wait(timeout=5)
        overlapping = upsert(parallel, event_ids["overlap-other-event"])
        release.set()
        first = first_future.result(timeout=5)
    assert first["business_result"]["state"] == "UPSERTED"
    assert overlapping["errors"][0]["code"] == "EFFECT_ALREADY_IN_PROGRESS"
    assert sum(item.endswith("/opportunities/search") for item in requests) == 1
    assert sum(item.startswith("POST ") for item in requests) == 1

    started.clear()
    release.clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(upsert, same_event, event_ids["same-event"])
        assert started.wait(timeout=5)
        overlapping_same_event = upsert(same_event, event_ids["same-event"])
        release.set()
        first_same_event = first_future.result(timeout=5)
    assert first_same_event["business_result"]["state"] == "UPSERTED"
    assert overlapping_same_event["errors"][0]["code"] == "EFFECT_ALREADY_IN_PROGRESS"
    assert sum(item.endswith("/opportunities/search") for item in requests) == 2
    assert sum(item.startswith("POST ") for item in requests) == 2

    unknown_result = upsert(unknown, event_ids["unknown"])
    retry_result = upsert(unknown, event_ids["unknown"])
    assert unknown_result["errors"][0]["code"] == "REMOTE_OUTCOME_UNKNOWN"
    assert retry_result["errors"][0]["code"] == "EFFECT_RECONCILIATION_REQUIRED"
    assert sum(item.endswith("/opportunities/search") for item in requests) == 3
    assert sum(item.startswith("POST ") for item in requests) == 3

    unknown_event_key = event_keys[list(event_ids).index("unknown")]
    unknown_effect_key = GHLUpsertOpportunity._effect_key(
        location_id, unknown["contactId"], unknown["pipelineId"]
    )
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT state, error_code FROM workflow_ghl_opportunity_upserts WHERE event_key = %s",
                           (unknown_event_key,))
            assert cursor.fetchone() == ("UNKNOWN", "REMOTE_OUTCOME_UNKNOWN")
            cursor.execute("""SELECT error_code, resolved_at IS NULL
                              FROM workflow_external_effect_reconciliations WHERE effect_key = %s""",
                           (unknown_effect_key,))
            assert cursor.fetchone() == ("REMOTE_OUTCOME_UNKNOWN", True)

    first_success = upsert(success, event_ids["success"])
    cached_success = upsert(success, event_ids["success"])
    changed_payload = upsert(success, event_ids["success"], name="Changed synthetic opportunity")
    assert first_success["business_result"]["side_effect"] is True
    assert cached_success["business_result"]["cached"] is True
    assert cached_success["business_result"]["side_effect"] is False
    assert changed_payload["errors"][0]["code"] == "OPPORTUNITY_EVENT_PAYLOAD_CONFLICT"
    assert sum(item.endswith("/opportunities/search") for item in requests) == 4
    assert sum(item.startswith("POST ") for item in requests) == 4
finally:
    client.close()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM workflow_external_effect_reconciliations WHERE effect_key = ANY(%s)",
                (effect_keys,),
            )
            cursor.execute("DELETE FROM workflow_ghl_opportunity_upserts WHERE event_key = ANY(%s)",
                           (event_keys,))
            cursor.execute("SELECT count(*) FROM workflow_external_effect_reconciliations WHERE effect_key = ANY(%s)",
                           (effect_keys,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT count(*) FROM workflow_ghl_opportunity_upserts WHERE event_key = ANY(%s)",
                           (event_keys,))
            assert cursor.fetchone()[0] == 0

print({
    "synthetic_http_transport": True,
    "different_events_same_contact_pipeline_overlap": "ONE_SEARCH_AND_ONE_CREATE",
    "same_event_overlap": "ONE_SEARCH_AND_ONE_CREATE",
    "unknown_write_retry": "BLOCKED_FOR_RECONCILIATION",
    "both_unknown_ledgers_inspected": True,
    "confirmed_success_replay": "CACHED_WITHOUT_PROVIDER_REQUEST",
    "changed_payload_replay": "HELD_FOR_REVIEW",
    "reconciliation_rows_removed": True,
})
