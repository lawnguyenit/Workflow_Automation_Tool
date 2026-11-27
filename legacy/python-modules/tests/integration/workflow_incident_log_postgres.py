"""Synthetic PostgreSQL checks for sanitized, idempotent workflow incident handling.

Run from the host with:
  Get-Content -Raw tests/integration/workflow_incident_log_postgres.py | docker compose exec -T api python -

No external provider is contacted. The harness refuses cleanup outside the local
Compose workflow_automation database and removes all generated rows.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from workflow_automation.api import create_app
from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.infrastructure.postgres_workflow_incidents import (
    PostgresWorkflowIncidentLog,
    WorkflowIncidentLogError,
)


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

run_id = f"synthetic-incident-{uuid4()}"
workflow_id = "synthetic_incident_integration"
event_key = hashlib.sha256(
    f"workflow-incident:v1\0{workflow_id}\0{run_id}\0{1}".encode()
).hexdigest()
api_run_id = f"synthetic-incident-api-{uuid4()}"
api_workflow_id = "lead_management_google_forms_api"
api_event_key = hashlib.sha256(
    f"workflow-incident:v1\0{api_workflow_id}\0{api_run_id}\0{1}".encode()
).hexdigest()
log = PostgresWorkflowIncidentLog(dsn=database_url)
failure = {
    "status": "FAILURE",
    "steps": {"crm": {
        "module_id": "crm.ghl_upsert_contact", "status": "FAILURE",
        "business_result": {"email": "private-person@example.test"},
        "errors": [{
            "code": "REMOTE_OUTCOME_UNKNOWN", "category": "EXTERNAL",
            "retryability": "UNKNOWN", "message": "private-person@example.test raw provider body",
            "details": {"token": "synthetic-secret-marker"},
        }],
    }},
}


def record() -> dict:
    return log.record_result(workflow_id=workflow_id, run_id=run_id, attempt=1,
                             mode="MOCK", result=failure)


try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _index: record(), range(2)))
    assert first["incident_id"] == second["incident_id"]
    assert {first["duplicate"], second["duplicate"]} == {False, True}

    try:
        log.record_result(workflow_id=workflow_id, run_id=run_id, attempt=1,
                           mode="MOCK", result={"status": "REVIEW_REQUIRED", "steps": {}})
    except WorkflowIncidentLogError as exc:
        assert exc.code == "INCIDENT_EVENT_CONFLICT"
    else:
        raise AssertionError("Changed data reused one workflow-run attempt key")

    incident_id = first["incident_id"]
    open_rows = log.list_incidents(limit=100)
    stored = next(item for item in open_rows if item["incident_id"] == incident_id)
    assert stored["diagnostics"] == {"crm": {
        "module_id": "crm.ghl_upsert_contact", "status": "FAILURE",
        "errors": [{"code": "REMOTE_OUTCOME_UNKNOWN", "category": "EXTERNAL",
                    "retryability": "UNKNOWN"}],
    }}
    assert "private-person@example.test" not in repr(stored)
    assert "synthetic-secret-marker" not in repr(stored)

    resolved = log.resolve(incident_id=incident_id, resolution_code="PROVIDER_STATE_CONFIRMED")
    repeated_resolution = log.resolve(incident_id=incident_id, resolution_code="DISMISSED")
    assert resolved["resolved_at"] == repeated_resolution["resolved_at"]
    assert repeated_resolution["resolution_code"] == "PROVIDER_STATE_CONFIRMED"
    assert all(item["incident_id"] != incident_id for item in log.list_incidents(limit=100))

    os.environ["WORKFLOW_API_KEY"] = f"synthetic-key-{uuid4()}"
    api_key = os.environ["WORKFLOW_API_KEY"]
    client = TestClient(create_app())
    context = ExecutionContext(workflow_id=api_workflow_id, run_id=api_run_id,
                               mode=RunMode.MOCK)
    response = client.post(
        f"/v1/workflows/{api_workflow_id}/runs",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"payload": {"form_id": "", "form": {}, "response": {}},
              "context": context.model_dump(mode="json")},
    )
    assert response.status_code == 200
    api_incident = response.json()["incident"]
    assert api_incident["status"] in {"FAILURE", "REVIEW_REQUIRED"}
    listed = client.get("/v1/incidents", headers={"Authorization": f"Bearer {api_key}"})
    assert any(item["incident_id"] == api_incident["incident_id"]
               for item in listed.json()["incidents"])
    resolved_api = client.post(
        f"/v1/incidents/{api_incident['incident_id']}/resolve",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"resolution_code": "DISMISSED"},
    )
    assert resolved_api.status_code == 200
finally:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM workflow_run_incidents WHERE event_key = ANY(%s)",
                           ([event_key, api_event_key],))
            cursor.execute("SELECT count(*) FROM workflow_run_incidents WHERE event_key = ANY(%s)",
                           ([event_key, api_event_key],))
            assert cursor.fetchone()[0] == 0

print({
    "same_attempt_concurrency": "ONE_IDEMPOTENT_INCIDENT",
    "changed_payload_replay": "CONFLICT_HELD",
    "stored_fields": "PII_AND_ERROR_TEXT_REDACTED",
    "resolution": "IDEMPOTENT_AND_OPERATOR_CODE_ONLY",
    "api_integration": "INCIDENT_LOGGED_LISTED_AND_RESOLVED",
    "synthetic_rows_removed": True,
})
