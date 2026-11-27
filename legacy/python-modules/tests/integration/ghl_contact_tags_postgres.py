"""Synthetic GHL additive-tag writes through the real Compose PostgreSQL guard.

Run from the host with:
  Get-Content -Raw tests/integration/ghl_contact_tags_postgres.py | docker compose exec -T api python -

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
from workflow_automation.infrastructure.postgres_effect_guard import PostgresExternalEffectGuard
from workflow_automation.modules.crm.ghl_contact_tags import GHLContactTags


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

location_id = "synthetic-tag-integration-location"
contact_id = "synthetic-tag-integration-contact"
unknown_contact_id = "synthetic-tag-unknown-contact"
contact_key = hashlib.sha256(
    "\0".join(("ghl-v3-contact-tags", location_id, contact_id)).encode()
).hexdigest()
unknown_key = hashlib.sha256(
    "\0".join(("ghl-v3-contact-tags", location_id, unknown_contact_id)).encode()
).hexdigest()
started = threading.Event()
release = threading.Event()
requests: list[str] = []


def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request.url.path)
    if request.url.path.endswith(unknown_contact_id + "/tags"):
        raise httpx.ReadTimeout("synthetic unknown write", request=request)
    if request.url.path.endswith(contact_id + "/tags"):
        started.set()
        if not release.wait(timeout=5):
            raise httpx.ReadTimeout("synthetic integration timeout", request=request)
        return httpx.Response(201, json={"tags": ["existing", "qualified"]})
    return httpx.Response(404, json={})


client = httpx.Client(transport=httpx.MockTransport(handler))
context = ExecutionContext(workflow_id="synthetic_ghl_contact_tags", mode=RunMode.REAL)
module = GHLContactTags(location_id=location_id, bearer_token="synthetic-token",
                        http_client=client, effect_guard=PostgresExternalEffectGuard(dsn=database_url))


def add_tags(target_id: str) -> dict:
    return module.execute({"contact_id": target_id, "tags": ["synthetic-qualified"]}, context).model_dump()


try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(add_tags, contact_id)
        assert started.wait(timeout=5)
        overlapping = add_tags(contact_id)
        release.set()
        first = first_future.result(timeout=5)
    assert first["business_result"]["state"] == "TAGS_ADDED"
    assert overlapping["errors"][0]["code"] == "CONTACT_TAG_WRITE_IN_PROGRESS"
    assert sum(path.endswith(contact_id + "/tags") for path in requests) == 1

    unknown = add_tags(unknown_contact_id)
    blocked_retry = add_tags(unknown_contact_id)
    assert unknown["errors"][0]["code"] == "REMOTE_OUTCOME_UNKNOWN"
    assert blocked_retry["errors"][0]["code"] == "CONTACT_TAG_RECONCILIATION_REQUIRED"
    assert sum(path.endswith(unknown_contact_id + "/tags") for path in requests) == 1
finally:
    client.close()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM workflow_external_effect_reconciliations WHERE effect_key = ANY(%s)",
                ([contact_key, unknown_key],),
            )

print({
    "synthetic_http_transport": True,
    "same_contact_overlapping_calls": "ONE_PROVIDER_REQUEST",
    "unknown_write_retry": "BLOCKED_FOR_RECONCILIATION",
    "reconciliation_rows_removed": True,
})
