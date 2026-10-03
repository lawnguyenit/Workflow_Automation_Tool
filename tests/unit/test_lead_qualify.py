import json

import httpx

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.infrastructure.postgres_lead_qualification_ledger import QualificationInputConflict
from workflow_automation.modules.processing.lead_qualify import LeadQualify


_CRITERIA = {
    "rubric": "Classify only the requested workflow service; missing fit evidence must remain uncertain.",
    "classifications": [
        {"name": "qualified", "description": "Request fits the offered automation service."},
        {"name": "not_a_fit", "description": "Request clearly falls outside the offered service."},
    ],
    "required_fields": ["name", "email", "service_requested", "description"],
}
_LEAD = {
    "source_system": "synthetic_form", "source_account": "test", "source_event_id": "event-1",
    "name": "Ada Example", "email": "ada@example.invalid", "phone": "",
    "service_requested": "Lead automation", "description": "We need to route web enquiries.",
    "qualification": {"status": "REVIEW_REQUIRED", "reasons": [], "classification": None,
                      "summary": None, "model_used": None},
    "payload_fingerprint": "0" * 64,
}
_OUTPUT = {"classification": "qualified", "summary": "Wants to route web enquiries.", "confidence": 0.91,
           "missing_information": [], "reasons": []}


class MemoryQualificationLedger:
    def __init__(self):
        self.calls = {}
        self.provider_calls = 0

    def run_once(self, *, event_key, input_fingerprint, criteria_fingerprint, provider_model,
                 prompt_version, max_calls_per_day, operation):
        identity = (input_fingerprint, criteria_fingerprint, provider_model, prompt_version)
        if event_key in self.calls:
            old_identity, result = self.calls[event_key]
            if old_identity != identity:
                raise QualificationInputConflict("synthetic source event conflict")
            return result
        if self.provider_calls >= max_calls_per_day:
            raise AssertionError("test ledger cap unexpectedly exhausted")
        self.provider_calls += 1
        result = operation()
        self.calls[event_key] = (identity, result)
        return result


def _context(mode=RunMode.MOCK):
    return ExecutionContext(workflow_id="lead_management_intake", mode=mode)


def test_mock_fixture_classifies_only_with_explicit_valid_rubric():
    result = LeadQualify().execute({"lead": _LEAD, "criteria": _CRITERIA, "mock_result": _OUTPUT}, _context())
    assert result.status.value == "SUCCESS"
    assert result.business_result["lead"]["qualification"] == {
        "status": "CLASSIFIED", "reasons": [], "classification": "qualified",
        "summary": "Wants to route web enquiries.", "model_used": "fixture",
    }
    assert result.business_result["prompt_version"] == "lead_qualification_v1"


def test_missing_criteria_or_missing_required_fields_hold_for_review():
    module = LeadQualify()
    no_criteria = module.execute({"lead": _LEAD}, _context())
    missing_email = module.execute({"lead": {**_LEAD, "email": ""}, "criteria": _CRITERIA,
                                     "mock_result": _OUTPUT}, _context())
    assert no_criteria.business_result["lead"]["qualification"]["reasons"] == [
        "qualification_criteria_not_configured"
    ]
    assert missing_email.business_result["lead"]["qualification"]["reasons"] == ["missing_email"]
    assert no_criteria.status.value == missing_email.status.value == "PARTIAL"


def test_low_confidence_and_unlisted_classification_are_held_for_review():
    module = LeadQualify()
    low_confidence = module.execute({"lead": _LEAD, "criteria": _CRITERIA,
                                    "mock_result": {**_OUTPUT, "confidence": 0.3}}, _context())
    invalid_class = module.execute({"lead": _LEAD, "criteria": _CRITERIA,
                                    "mock_result": {**_OUTPUT, "classification": "invented"}}, _context())
    assert low_confidence.business_result["lead"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert "low_confidence" in low_confidence.business_result["lead"]["qualification"]["reasons"]
    assert invalid_class.business_result["lead"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert invalid_class.errors[0].code == "CLASSIFICATION_NOT_IN_RUBRIC"


def test_null_classification_has_actionable_unclear_fit_reason():
    result = LeadQualify().execute({"lead": _LEAD, "criteria": _CRITERIA,
                                    "mock_result": {**_OUTPUT, "classification": None}}, _context())
    qualification = result.business_result["lead"]["qualification"]
    assert qualification["status"] == "REVIEW_REQUIRED"
    assert qualification["reasons"] == ["unclear_fit"]


def test_summary_redacts_exact_contact_identifiers_before_persistence_or_output():
    output = {**_OUTPUT, "summary": "Ada Example asked us to call +1 (555) 010-2030 at ada@example.invalid."}
    result = LeadQualify().execute({"lead": {**_LEAD, "phone": "+15550102030"}, "criteria": _CRITERIA,
                                    "mock_result": output}, _context())
    summary = result.business_result["lead"]["qualification"]["summary"]
    assert "Ada Example" not in summary
    assert "ada@example.invalid" not in summary
    assert "555" not in summary
    assert summary.count("[redacted]") == 3


def test_real_request_uses_strict_schema_and_excludes_direct_contact_fields():
    observed = {}

    def handler(request):
        observed["body"] = json.loads(request.read())
        observed["headers"] = request.headers
        return httpx.Response(200, json={"output_text": json.dumps(_OUTPUT)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ledger = MemoryQualificationLedger()
    module = LeadQualify(api_key="test-token", model="test-model", qualification_ledger=ledger,
                         max_calls_per_day=3, http_client=client)
    try:
        result = module.execute({"lead": _LEAD, "criteria": _CRITERIA}, _context(RunMode.REAL))
    finally:
        client.close()
    body = observed["body"]
    prompt_data = json.dumps(body["input"], ensure_ascii=False)
    assert observed["headers"]["Authorization"] == "Bearer test-token"
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert "ada@example.invalid" not in prompt_data
    assert "Ada Example" not in prompt_data
    assert result.business_result["lead"]["qualification"]["model_used"] == "test-model"
    assert ledger.provider_calls == 1


def test_real_qualification_reuses_cached_result_and_rejects_changed_event_content():
    calls = []

    def handler(_request):
        calls.append(True)
        return httpx.Response(200, json={"output_text": json.dumps(_OUTPUT)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ledger = MemoryQualificationLedger()
    module = LeadQualify(api_key="test-token", model="test-model", qualification_ledger=ledger,
                         max_calls_per_day=3, http_client=client)
    try:
        first = module.execute({"lead": _LEAD, "criteria": _CRITERIA}, _context(RunMode.REAL))
        retry = module.execute({"lead": _LEAD, "criteria": _CRITERIA}, _context(RunMode.REAL))
        changed = module.execute({"lead": {**_LEAD, "description": "changed request"},
                                  "criteria": _CRITERIA}, _context(RunMode.REAL))
    finally:
        client.close()
    assert first.business_result["lead"]["qualification"] == retry.business_result["lead"]["qualification"]
    assert calls == [True]
    assert changed.business_result["lead"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert changed.errors[0].code == "QUALIFICATION_INPUT_CONFLICT"


def test_real_mode_fails_closed_without_persistent_ledger_or_explicit_daily_cap():
    calls = []

    def handler(_request):
        calls.append(True)
        return httpx.Response(200, json={"output_text": json.dumps(_OUTPUT)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        no_ledger = LeadQualify(api_key="test-token", model="test-model", http_client=client)
        no_limit = LeadQualify(api_key="test-token", model="test-model",
                               qualification_ledger=MemoryQualificationLedger(), http_client=client)
        first = no_ledger.execute({"lead": _LEAD, "criteria": _CRITERIA}, _context(RunMode.REAL))
        second = no_limit.execute({"lead": _LEAD, "criteria": _CRITERIA}, _context(RunMode.REAL))
    finally:
        client.close()
    assert first.errors[0].code == "qualification_ledger_or_daily_limit_not_configured"
    assert second.errors[0].code == "qualification_ledger_or_daily_limit_not_configured"
    assert calls == []


def test_model_timeout_is_sanitized_and_not_automatically_retried():
    calls = []

    def handler(_request):
        calls.append(True)
        raise httpx.ReadTimeout("untrusted transport details")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    module = LeadQualify(api_key="test-token", model="test-model",
                         qualification_ledger=MemoryQualificationLedger(), max_calls_per_day=3,
                         http_client=client)
    try:
        result = module.execute({"lead": _LEAD, "criteria": _CRITERIA}, _context(RunMode.REAL))
    finally:
        client.close()
    assert calls == [True]
    assert result.status.value == "PARTIAL"
    assert result.errors[0].code == "LLM_OUTCOME_UNKNOWN"
    assert result.errors[0].retryability.value == "UNKNOWN"
    assert "untrusted transport details" not in str(result.model_dump())


def test_invalid_rubric_and_non_json_mock_output_do_not_invoke_a_model():
    module = LeadQualify()
    invalid_rubric = module.execute({"lead": _LEAD,
                                     "criteria": {**_CRITERIA, "classifications": []}}, _context())
    invalid_fixture = module.execute({"lead": _LEAD, "criteria": _CRITERIA,
                                      "mock_result": {**_OUTPUT, "confidence": float("nan")}}, _context())
    assert invalid_rubric.business_result["lead"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert invalid_fixture.business_result["lead"]["qualification"]["status"] == "REVIEW_REQUIRED"
    assert invalid_fixture.errors[0].code == "INVALID_QUALIFICATION_OUTPUT"
