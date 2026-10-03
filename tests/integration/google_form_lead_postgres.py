"""Synthetic Google Form row mapping through the composed lead workflow.

Run from the host with:
  Get-Content -Raw tests/integration/google_form_lead_postgres.py | docker compose exec -T api python -

This exercises the real Compose PostgreSQL store. It does not contact Google,
activate n8n, or read/write the user's Google Form.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import psycopg

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.input.google_form_response import GoogleFormResponseRow
from workflow_automation.registry_factory import build_registry
from workflow_automation.runner import WorkflowRunner


database_url = os.environ.get("DATABASE_URL", "")
if not database_url or urlparse(database_url).path != "/workflow_automation":
    raise RuntimeError("Refusing cleanup outside the Compose workflow_automation database")

synthetic_spreadsheet = "syntheticSheetForIntegration"
synthetic_tab = 99017
synthetic_account = "synthetic-form-integration"
row_number = 100_000_000 + (uuid.uuid4().int % 800_000_000)
source_event_id = f"{synthetic_spreadsheet}:{synthetic_tab}:row:{row_number}"


def lead_row(*, description: str = "Synthetic integration lead.") -> dict:
    return {
        "Dấu thời gian": "2026-10-03 10:00:00",
        "Tên đầy đủ": "Synthetic Form Lead",
        "Email address": "synthetic@example.invalid",
        "Phone (optional)": "+15550102030",
        "Service requested": "Workflow automation",
        "Describe your request": description,
        "row_number": row_number,
        "change_type": "added",
    }


registry = build_registry()
registry.bind_instance_module(
    "portfolio.google_form_response_map",
    GoogleFormResponseRow(spreadsheet_id=synthetic_spreadsheet, sheet_id=synthetic_tab,
                          source_account=synthetic_account),
)
runner = WorkflowRunner(registry)
context = ExecutionContext(workflow_id="lead_management_google_form", mode=RunMode.MOCK)


def run(description: str = "Synthetic integration lead.") -> dict:
    return runner.run("portfolio.lead_management_google_form", {
        "row_number": row_number,
        "change_type": "added",
        "row": lead_row(description=description),
        "criteria": {
            "rubric": "Synthetic integration rubric only.",
            "classifications": [
                {"name": "qualified", "description": "Fits the synthetic automation service."},
                {"name": "not_a_fit", "description": "Clearly outside the synthetic service."},
            ],
            "required_fields": ["name", "email", "service_requested", "description"],
        },
        "mock_qualification": {
            "classification": "qualified", "summary": "Synthetic request for automation.",
            "confidence": 0.9, "missing_information": [], "reasons": [],
        },
        "replay_qualification": None,
    }, context)


try:
    inserted = run()
    duplicate = run()
    conflict = run("Changed text under the same form row key.")
    if "store" not in inserted["steps"]:
        summary = {name: {"status": step["status"],
                          "errors": [error["code"] for error in step.get("errors", [])]}
                   for name, step in inserted["steps"].items()}
        raise RuntimeError(f"Synthetic lead workflow stopped before storage: {summary}")
    assert inserted["steps"]["map"]["status"] == "SUCCESS"
    assert inserted["steps"]["store"]["business_result"]["state"] == "STORED"
    assert duplicate["steps"]["store"]["business_result"]["state"] == "DUPLICATE"
    assert conflict["steps"]["store"]["business_result"]["state"] == "PAYLOAD_CONFLICT_REVIEW"
finally:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM workflow_leads WHERE source_system = %s AND source_account = %s AND source_event_id = %s",
                ("google_forms", synthetic_account, source_event_id),
            )

print({
    "source": "synthetic Google Sheets Trigger row",
    "mapped": True,
    "first_write": "STORED",
    "same_row_retry": "DUPLICATE",
    "changed_row_conflict": "PAYLOAD_CONFLICT_REVIEW",
    "synthetic_rows_removed": True,
})
