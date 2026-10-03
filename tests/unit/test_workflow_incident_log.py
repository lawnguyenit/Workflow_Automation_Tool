from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workflow_automation.api import create_app
from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.infrastructure.postgres_workflow_incidents import (
    PostgresWorkflowIncidentLog,
    WorkflowIncidentLogError,
)


def test_incident_diagnostics_drop_messages_details_and_business_data():
    safe = PostgresWorkflowIncidentLog._sanitize_steps({
        "store": {
            "module_id": "lead.store",
            "status": "FAILURE",
            "business_result": {"email": "person@example.test"},
            "errors": [{
                "code": "DATABASE_WRITE_UNKNOWN",
                "category": "STORAGE",
                "retryability": "UNKNOWN",
                "message": "Person@example.test: raw provider response",
                "details": {"token": "secret-value"},
            }],
        },
    })

    assert safe == {"store": {
        "module_id": "lead.store", "status": "FAILURE",
        "errors": [{"code": "DATABASE_WRITE_UNKNOWN", "category": "STORAGE",
                    "retryability": "UNKNOWN"}],
    }}
    assert "person@example.test" not in repr(safe)
    assert "secret-value" not in repr(safe)


def test_incident_log_skips_success_and_non_persistent_modes_without_database():
    log = PostgresWorkflowIncidentLog(dsn=None)
    failure = {"status": "FAILURE", "steps": {}}

    assert log.record_result(workflow_id="example", run_id="run-1", attempt=1,
                             mode="DRY_RUN", result=failure) is None
    assert log.record_result(workflow_id="example", run_id="run-2", attempt=1,
                             mode="MOCK", result={"status": "SUCCESS", "steps": {}}) is None


def test_incident_log_validates_bounded_identifiers_and_queries():
    log = PostgresWorkflowIncidentLog(dsn=None)
    with pytest.raises(WorkflowIncidentLogError, match="INVALID_WORKFLOW_ID"):
        log.record_result(workflow_id="person@example.test", run_id="run", attempt=1,
                          mode="MOCK", result={"status": "FAILURE", "steps": {}})
    with pytest.raises(WorkflowIncidentLogError, match="INVALID_LIMIT"):
        log.list_incidents(limit=101)
    with pytest.raises(WorkflowIncidentLogError, match="INVALID_INCIDENT_ID"):
        log.resolve(incident_id="not-a-uuid", resolution_code="DISMISSED")
    with pytest.raises(WorkflowIncidentLogError, match="INVALID_RESOLUTION_CODE"):
        log.resolve(incident_id="a8fb2c07-77d8-43fd-885a-a0d008188b66", resolution_code="retry now")


class _FakeIncidentLog:
    def __init__(self) -> None:
        self.recorded: list[dict] = []

    def record_result(self, **kwargs):
        self.recorded.append(kwargs)
        return {"incident_id": "8b3bd683-393b-4a50-9f19-8b44d5bf34f2",
                "status": kwargs["result"]["status"], "duplicate": False, "resolved": False}

    def list_incidents(self, **kwargs):
        return [{"incident_id": "8b3bd683-393b-4a50-9f19-8b44d5bf34f2", "status": "FAILURE"}]

    def get_incident(self, **kwargs):
        if kwargs["incident_id"] != "8b3bd683-393b-4a50-9f19-8b44d5bf34f2":
            return None
        return {"incident_id": kwargs["incident_id"], "status": "FAILURE"}

    def resolve(self, **kwargs):
        if kwargs["incident_id"] != "8b3bd683-393b-4a50-9f19-8b44d5bf34f2":
            return {"found": False, "resolved": False}
        return {"found": True, "incident_id": kwargs["incident_id"],
                "resolved_at": "2026-10-03T00:00:00+00:00", "resolution_code": kwargs["resolution_code"]}


def test_incident_routes_require_bearer_and_allow_resolution_codes(monkeypatch):
    monkeypatch.setenv("WORKFLOW_API_KEY", "test-only-api-key")
    client = TestClient(create_app())
    fake = _FakeIncidentLog()
    client.app.state.incident_log = fake

    assert client.get("/v1/incidents").status_code == 401
    headers = {"Authorization": "Bearer test-only-api-key"}
    assert client.get("/v1/incidents", headers=headers).json()["incidents"][0]["status"] == "FAILURE"
    incident_id = "8b3bd683-393b-4a50-9f19-8b44d5bf34f2"
    assert client.get(f"/v1/incidents/{incident_id}", headers=headers).json()["status"] == "FAILURE"
    assert client.get("/v1/incidents?limit=101", headers=headers).status_code == 422

    response = client.post(f"/v1/incidents/{incident_id}/resolve",
                           headers=headers, json={"resolution_code": "PROVIDER_STATE_CONFIRMED"})
    assert response.status_code == 200
    assert response.json()["resolution_code"] == "PROVIDER_STATE_CONFIRMED"
    assert client.post(f"/v1/incidents/{incident_id}/resolve",
                       headers=headers, json={"resolution_code": "free text"}).status_code == 422


def test_run_route_records_sanitized_incident_without_request_payload(monkeypatch):
    monkeypatch.setenv("WORKFLOW_API_KEY", "test-only-api-key")
    client = TestClient(create_app())
    fake = _FakeIncidentLog()
    client.app.state.incident_log = fake

    class _Runner:
        def run(self, *_args, **_kwargs):
            return {
                "workflow_id": "lead_management_google_forms_api",
                "workflow_instance_id": "portfolio.lead_management_google_forms_api",
                "workflow_version": "1",
                "status": "FAILURE",
                "context": ExecutionContext(workflow_id="lead_management_google_forms_api",
                                            mode=RunMode.MOCK).model_dump(mode="json"),
                "steps": {"store": {
                    "module_id": "lead.store", "status": "FAILURE",
                    "business_result": {"email": "person@example.test"},
                    "errors": [{"code": "DATABASE_WRITE_UNKNOWN", "category": "STORAGE",
                                "message": "person@example.test", "retryability": "UNKNOWN"}],
                }},
                "skipped_steps": [], "result": None,
            }

    client.app.state.runner = _Runner()
    context = ExecutionContext(workflow_id="lead_management_google_forms_api", mode=RunMode.MOCK)
    response = client.post(
        "/v1/workflows/lead_management_google_forms_api/runs",
        headers={"Authorization": "Bearer test-only-api-key"},
        json={"context": context.model_dump(mode="json"), "payload": {"private": "person@example.test"}},
    )

    assert response.status_code == 200
    assert response.json()["incident"]["status"] == "FAILURE"
    assert len(fake.recorded) == 1
    assert "private" not in repr(fake.recorded[0])
