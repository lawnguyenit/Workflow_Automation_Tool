from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.input.google_form_response import (
    DEFAULT_HEADER_MAP,
    GoogleFormResponseRow,
)


SHEET_ID = "syntheticSpreadsheet123"
TAB_ID = 99017


def _module(**changes):
    values = {"spreadsheet_id": SHEET_ID, "sheet_id": TAB_ID,
              "source_account": "portfolio-lead-form"}
    values.update(changes)
    return GoogleFormResponseRow(**values)


def _payload(row_number=2, **changes):
    row = {
        "Dấu thời gian": "2026-10-03 10:00:00",
        "Tên đầy đủ": "Taylor Example",
        "Email address": "taylor@example.invalid",
        "Phone (optional)": "+15550102030",
        "Service requested": "Workflow automation",
        "Describe your request": "Synthetic test request.",
        "row_number": row_number,
        "change_type": "added",
    }
    row.update(changes)
    return {"row_number": row_number, "row": row, "change_type": "added"}


def test_maps_observed_google_form_headers_to_a_stable_lead_event_key():
    result = _module().execute(_payload(), ExecutionContext(workflow_id="test", mode=RunMode.DRY_RUN))
    assert result.status == "SUCCESS"
    lead = result.business_result["lead"]
    assert lead == {
        "source_system": "google_forms",
        "source_account": "portfolio-lead-form",
        "source_event_id": f"{SHEET_ID}:{TAB_ID}:row:2",
        "name": "Taylor Example",
        "email": "taylor@example.invalid",
        "phone": "+15550102030",
        "service_requested": "Workflow automation",
        "description": "Synthetic test request.",
    }
    assert result.metrics == {"row_number": 2, "mapped_fields": 5}
    assert set(DEFAULT_HEADER_MAP) == {"name", "email", "phone", "service_requested", "description"}


def test_missing_values_are_preserved_as_empty_for_lead_intake_review():
    payload = _payload(**{"Email address": "", "Phone (optional)": ""})
    result = _module().execute(payload, ExecutionContext(workflow_id="test"))
    lead = result.business_result["lead"]
    assert lead["email"] == ""
    assert lead["phone"] == ""


def test_header_drift_fails_closed_without_returning_row_content():
    payload = _payload()
    del payload["row"]["Email address"]
    result = _module().execute(payload, ExecutionContext(workflow_id="test"))
    assert result.status == "FAILURE"
    assert result.errors[0].code == "FORM_HEADERS_CHANGED"
    assert "Taylor Example" not in str(result.model_dump())


def test_non_added_row_number_and_non_text_contact_values_are_rejected():
    context = ExecutionContext(workflow_id="test")
    bad_row_number = _module().execute(_payload(1), context)
    bad_value = _module().execute(_payload(**{"Email address": 42}), context)
    updated_row = _payload()
    updated_row["change_type"] = "updated"
    bad_change_type = _module().execute(updated_row, context)
    assert bad_row_number.errors[0].code == "INVALID_ROW_NUMBER"
    assert bad_value.errors[0].code == "INVALID_ROW_VALUE"
    assert bad_change_type.errors[0].code == "UNSUPPORTED_CHANGE_TYPE"


def test_mapper_requires_spreadsheet_and_tab_identity():
    context = ExecutionContext(workflow_id="test")
    missing = GoogleFormResponseRow(spreadsheet_id=None, sheet_id=None)
    result = missing.execute(_payload(), context)
    assert result.status == "FAILURE"
    assert result.errors[0].code == "SOURCE_NOT_CONFIGURED"


def test_same_row_always_produces_the_same_source_event_id():
    context = ExecutionContext(workflow_id="test")
    first = _module().execute(_payload(29), context).business_result["lead"]["source_event_id"]
    retry = _module().execute(_payload(29), context).business_result["lead"]["source_event_id"]
    assert first == retry
