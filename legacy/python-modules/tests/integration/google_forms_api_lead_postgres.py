"""Synthetic Forms API response mapping through the composed lead workflow.

Run from the host with:
  Get-Content -Raw tests/integration/google_forms_api_lead_postgres.py | docker compose exec -T api python -

This exercises the real Compose PostgreSQL store. It does not contact Google,
activate n8n, or read/write the user's Google Form.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import psycopg

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.input.google_forms_api_response import DEFAULT_FIELD_TITLES
from workflow_automation.registry_factory import build_registry
from workflow_automation.runner import WorkflowRunner


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

form_id = "syntheticFormsApiIntegration"
response_id = f"syntheticResponse{uuid.uuid4().hex}"
source_account = "portfolio-lead-form"
source_event_id = f"{form_id}:{response_id}"
items = [
    {"title": title, "questionItem": {"question": {"questionId": f"q-{field}"}}}
    for field, title in DEFAULT_FIELD_TITLES.items()
]
values = {
    "name": "Synthetic Forms API Lead",
    "email": "synthetic-forms-api@example.invalid",
    "phone": "+15550102030",
    "service_requested": "Workflow automation",
    "description": "Synthetic Forms API PostgreSQL integration.",
}
response = {
    "responseId": response_id,
    "lastSubmittedTime": "2026-10-03T03:00:00Z",
    "answers": {
        f"q-{field}": {"textAnswers": {"answers": [{"value": value}]}}
        for field, value in values.items()
    },
}

registry = build_registry()
runner = WorkflowRunner(registry)
context = ExecutionContext(workflow_id="lead_management_google_forms_api", mode=RunMode.MOCK)


def run(description: str = values["description"]) -> dict:
    changed_response = {**response, "answers": {**response["answers"]}}
    changed_response["answers"]["q-description"] = {
        "textAnswers": {"answers": [{"value": description}]}
    }
    return runner.run("portfolio.lead_management_google_forms_api", {
        "form_id": form_id,
        "form": {"formId": form_id, "items": items},
        "response": changed_response,
        "criteria": {
            "rubric": "Synthetic integration rubric only.",
            "classifications": [
                {"name": "qualified", "description": "Fits the synthetic service."},
                {"name": "not_a_fit", "description": "Outside the synthetic service."},
            ],
            "required_fields": ["name", "email", "service_requested", "description"],
        },
        "mock_qualification": {
            "classification": "qualified", "summary": "Synthetic Forms API request.",
            "confidence": 0.9, "missing_information": [], "reasons": [],
        },
        "replay_qualification": None,
    }, context)


try:
    inserted = run()
    duplicate = run()
    conflict = run("Changed text under the same synthetic response ID.")
    assert inserted["steps"]["map"]["status"] == "SUCCESS"
    assert inserted["steps"]["store"]["business_result"]["state"] == "STORED"
    assert duplicate["steps"]["store"]["business_result"]["state"] == "DUPLICATE"
    assert conflict["steps"]["store"]["business_result"]["state"] == "PAYLOAD_CONFLICT_REVIEW"
finally:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM workflow_leads WHERE source_system = %s AND source_account = %s AND source_event_id = %s",
                ("google_forms_api", source_account, source_event_id),
            )

print({
    "source": "synthetic Google Forms API FormResponse",
    "mapped": True,
    "first_write": "STORED",
    "same_response_retry": "DUPLICATE",
    "changed_payload_conflict": "PAYLOAD_CONFLICT_REVIEW",
    "synthetic_rows_removed": True,
})
