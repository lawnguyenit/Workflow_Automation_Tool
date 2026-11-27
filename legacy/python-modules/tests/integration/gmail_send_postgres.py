"""Synthetic Gmail-send integration against the Compose PostgreSQL outbox.

Run from the host with:
  Get-Content -Raw tests/integration/gmail_send_postgres.py | docker compose exec -T api python -
The Gmail endpoint is always an httpx MockTransport; this script cannot send mail.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
import psycopg

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.output.gmail_send import GmailSend
from workflow_automation.modules.storage.postgres_store import PostgresStore


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing integration cleanup outside the Compose workflow_automation database")

store = PostgresStore()
account_scope = "synthetic-integration"
context = ExecutionContext(workflow_id="gmail-send-postgres-integration", mode=RunMode.REAL)
synthetic_keys: list[str] = []
lock = threading.Lock()


def key_hash(key: str) -> str:
    return hashlib.sha256(f"gmail-send-v1\0{account_scope}\0{key}".encode()).hexdigest()


def make_module(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    module = GmailSend(store=store, account_scope=account_scope, access_token="synthetic-token",
                       http_client=client)
    return module, client


def message(key: str) -> dict[str, str]:
    return {"idempotency_key": key, "to": "person@example.invalid", "subject": "Synthetic integration",
            "text_body": "Synthetic only; no actual Gmail request is possible in this test."}


def successful_provider(request: httpx.Request) -> httpx.Response:
    assert request.method == "POST"
    assert request.url.path == "/gmail/v1/users/me/messages/send"
    assert request.headers["Authorization"] == "Bearer synthetic-token"
    raw = json.loads(request.read())["raw"]
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    assert b"person@example.invalid" in decoded and b"Synthetic only" in decoded
    return httpx.Response(200, json={"id": "synthetic-gmail-message"})


def main() -> None:
    sends: list[bool] = []
    key = "confirmed-" + str(uuid.uuid4())
    synthetic_keys.append(key)

    def handler(request):
        sends.append(True)
        return successful_provider(request)

    module, client = make_module(handler)
    try:
        first = module.execute(message(key), context)
        replay = module.execute(message(key), context)
        changed = module.execute({**message(key), "text_body": "Changed body"}, context)
    finally:
        client.close()
    assert first.status == "SUCCESS" and first.business_result["side_effect"] is True
    assert replay.status == "SUCCESS" and replay.business_result["duplicate"] is True
    assert changed.status == "REVIEW_REQUIRED" and changed.errors[0].code == "OUTBOX_IDEMPOTENCY_KEY_REUSED"
    assert len(sends) == 1, sends
    with psycopg.connect(database_url) as db:
        state, payload = db.execute(
            "SELECT state, payload FROM notification_outbox WHERE idempotency_key = %s", (key_hash(key),)
        ).fetchone()
    assert state == "SENT" and payload == {}, (state, payload)

    race_key = "concurrent-" + str(uuid.uuid4())
    synthetic_keys.append(race_key)
    race_sends: list[bool] = []
    barrier = threading.Barrier(2)

    def slow_provider(_request):
        with lock:
            race_sends.append(True)
        time.sleep(0.4)
        return httpx.Response(200, json={"id": "synthetic-race-message"})

    modules = [make_module(slow_provider), make_module(slow_provider)]
    try:
        def execute(module):
            barrier.wait()
            return module.execute(message(race_key), context)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, [pair[0] for pair in modules]))
    finally:
        for _module, http_client in modules:
            http_client.close()
    assert len(race_sends) == 1, race_sends
    assert sum(result.status == "SUCCESS" for result in results) == 1
    assert sum(result.status == "REVIEW_REQUIRED" for result in results) == 1

    unknown_key = "unknown-" + str(uuid.uuid4())
    synthetic_keys.append(unknown_key)
    unknown_calls: list[bool] = []

    def uncertain_provider(_request):
        unknown_calls.append(True)
        return httpx.Response(500, json={"synthetic": True})

    uncertain_module, uncertain_client = make_module(uncertain_provider)
    try:
        uncertain = uncertain_module.execute(message(unknown_key), context)
        retry = uncertain_module.execute(message(unknown_key), context)
    finally:
        uncertain_client.close()
    assert uncertain.errors[0].code == "REMOTE_OUTCOME_UNKNOWN"
    assert retry.errors[0].code == "DELIVERY_REQUIRES_REVIEW"
    assert len(unknown_calls) == 1
    with psycopg.connect(database_url) as db:
        state = db.execute(
            "SELECT state FROM notification_outbox WHERE idempotency_key = %s", (key_hash(unknown_key),)
        ).fetchone()[0]
    assert state == "UNKNOWN"


try:
    main()
finally:
    if synthetic_keys:
        with psycopg.connect(database_url) as db:
            deleted = db.execute(
                "DELETE FROM notification_outbox WHERE idempotency_key = ANY(%s)",
                ([key_hash(key) for key in synthetic_keys],),
            ).rowcount
        assert deleted == len(synthetic_keys), (deleted, len(synthetic_keys))
print({"synthetic_gmail_calls": 1, "same_key_concurrent_calls": 1,
       "duplicate_and_unknown_holds_verified": True, "sent_payload_redacted": True,
       "synthetic_outbox_rows_removed": len(synthetic_keys)})
