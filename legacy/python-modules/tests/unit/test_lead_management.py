from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.processing.lead_intake import LeadIntake
from workflow_automation.modules.storage.postgres_lead_store import PostgresLeadStore


def test_lead_intake_normalizes_and_holds_for_human_review():
    result = LeadIntake().execute({"lead": {
        "source_system": "GoogleForm", "source_account": "portfolio",
        "source_event_id": "test-evt-1", "name": "  Ada Example ",
        "email": " ADA@EXAMPLE.COM ", "phone": "+1 (555) 010-2030",
        "service_requested": "Workflow setup", "description": "Synthetic enquiry",
    }}, ExecutionContext(workflow_id="lead_management_intake", mode=RunMode.MOCK))
    lead = result.business_result["lead"]
    assert result.status.value == "PARTIAL"
    assert lead["email"] == "ada@example.com"
    assert lead["phone"] == "+15550102030"
    assert lead["qualification"] == {
        "status": "REVIEW_REQUIRED",
        "reasons": ["qualification_criteria_not_configured"],
        "classification": None,
        "summary": None,
        "model_used": None,
    }
    assert len(lead["payload_fingerprint"]) == 64


def test_lead_intake_flags_missing_and_invalid_email_without_inventing_values():
    result = LeadIntake().execute({"lead": {
        "source_system": "form", "source_account": "test", "source_event_id": "test-evt-2",
        "name": "Ada", "email": "not-an-email", "service_requested": "", "description": "",
    }}, ExecutionContext(workflow_id="lead_management_intake", mode=RunMode.MOCK))
    lead = result.business_result["lead"]
    assert result.status.value == "PARTIAL"
    assert lead["service_requested"] == ""
    assert lead["qualification"]["reasons"] == [
        "missing_service_requested", "missing_description", "invalid_email_format",
        "qualification_criteria_not_configured",
    ]


def test_lead_intake_requires_stable_source_event_id():
    result = LeadIntake().execute({"lead": {"source_system": "form", "source_account": "test"}},
                                  ExecutionContext(workflow_id="lead_management_intake"))
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "SOURCE_EVENT_KEY_REQUIRED"


def test_lead_intake_endpoint_contract_keeps_review_status_without_returning_pii():
    lead = {
        "source_system": "form", "source_account": "test", "source_event_id": "test-evt-3",
        "name": "Ada", "email": "ada@example.invalid", "phone": "",
        "service_requested": "Automation", "description": "Synthetic only",
    }
    result = LeadIntake().execute({"lead": lead}, ExecutionContext(workflow_id="lead_management_intake"))
    assert result.status.value == "PARTIAL"
    assert result.business_result["lead"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert result.business_result["lead"]["email"] == "ada@example.invalid"


def test_lead_store_rejects_malformed_qualification_before_opening_database():
    intake = LeadIntake().execute({"lead": {
        "source_system": "form", "source_account": "test", "source_event_id": "test-evt-4",
        "name": "Ada", "email": "ada@example.invalid", "phone": "",
        "service_requested": "Automation", "description": "Synthetic only",
    }}, ExecutionContext(workflow_id="lead_management_intake"))
    lead = intake.business_result["lead"]
    lead["qualification"] = "malformed"
    opened = []

    def no_database(_dsn):
        opened.append(True)
        raise AssertionError("database must not be opened for malformed input")

    result = PostgresLeadStore(connection_factory=no_database).execute(
        {"lead": lead}, ExecutionContext(workflow_id="lead_management_intake", mode=RunMode.MOCK)
    )
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "INVALID_LEAD_RECORD"
    assert opened == []


def test_lead_store_rejects_a_forged_fingerprint_before_writing():
    intake = LeadIntake().execute({"lead": {
        "source_system": "form", "source_account": "test", "source_event_id": "test-evt-5",
        "name": "Ada", "email": "ada@example.invalid", "phone": "",
        "service_requested": "Automation", "description": "Synthetic only",
    }}, ExecutionContext(workflow_id="lead_management_intake"))
    lead = intake.business_result["lead"]
    lead["payload_fingerprint"] = "0" * 64
    result = PostgresLeadStore(connection_factory=lambda _dsn: None).execute(
        {"lead": lead}, ExecutionContext(workflow_id="lead_management_intake", mode=RunMode.MOCK)
    )
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "INVALID_LEAD_RECORD"


def test_lead_store_bounds_qualification_metadata_before_writing():
    intake = LeadIntake().execute({"lead": {
        "source_system": "form", "source_account": "test", "source_event_id": "test-evt-6",
        "name": "Ada", "email": "ada@example.invalid", "phone": "",
        "service_requested": "Automation", "description": "Synthetic only",
    }}, ExecutionContext(workflow_id="lead_management_intake"))
    lead = intake.business_result["lead"]
    lead["qualification"]["reasons"] = ["too-many"] * 21
    opened = []
    result = PostgresLeadStore(connection_factory=lambda _dsn: opened.append(True)).execute(
        {"lead": lead}, ExecutionContext(workflow_id="lead_management_intake", mode=RunMode.MOCK)
    )
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "INVALID_LEAD_RECORD"
    assert opened == []


def test_lead_intake_rejects_wrong_types_and_oversized_fields():
    module = LeadIntake()
    context = ExecutionContext(workflow_id="lead_management_intake")
    wrong_type = module.execute({"lead": {"name": 1}}, context)
    too_long = module.execute({"lead": {"description": "x" * 10_001}}, context)
    assert wrong_type.errors[0].code == "INVALID_FIELD_TYPE"
    assert too_long.errors[0].code == "FIELD_TOO_LONG"
