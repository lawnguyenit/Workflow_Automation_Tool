from __future__ import annotations

import json

import httpx

from workflow_automation.contracts import ExecutionContext, ResultState, RunMode
from workflow_automation.modules.processing.llm_structured_extract import LLMStructuredExtract


def _context(mode: RunMode = RunMode.MOCK) -> ExecutionContext:
    return ExecutionContext(workflow_id="test.invoice", mode=mode)


def _extracted(**overrides):
    value = {
        "document_type": "invoice",
        "sender": "Example Company",
        "invoice_number": "INV-1001",
        "amount": 1200.0,
        "currency": "USD",
        "confidence": 0.98,
        "missing_fields": [],
        "conflicts": [],
    }
    value.update(overrides)
    return value


def test_mock_extracts_invoice_from_supplied_text_without_external_call():
    result = LLMStructuredExtract().execute(
        {"documents": [{
            "source_id": "mail-1/attachment-1",
            "filename": "invoice.pdf",
            "text": "INVOICE\nSupplier: Example Company\nInvoice Number: INV-1001\nTotal Due: USD 1,200.00",
        }]},
        _context(),
    )

    assert result.status == ResultState.SUCCESS
    record = result.business_result["records"][0]
    assert {key: record[key] for key in ("document_type", "sender", "invoice_number", "amount", "currency")} == {
        "document_type": "invoice", "sender": "Example Company", "invoice_number": "INV-1001",
        "amount": 1200.0, "currency": "USD",
    }
    assert record["source_ids"] == ["mail-1/attachment-1"]
    assert record["status"] == "EXTRACTED"
    assert record["extraction_provider"] == "local_fixture"
    assert record["source_system"] is None
    assert LLMStructuredExtract.module_id == "llm.structured_extract"


def test_provenance_is_preserved_and_record_key_uses_it_instead_of_document_text():
    module = LLMStructuredExtract()
    provenance = {
        "source_system": "gmail_fixture",
        "source_account": "fixture:invoice-1",
        "source_message_id": "message:123",
        "source_attachment_id": "attachment:456",
    }
    first = module.execute(
        {"documents": [{"source_ids": ["attachment:456"], **provenance, "text": "Invoice one"}]}, _context()
    ).business_result["records"][0]
    second = module.execute(
        {"documents": [{"source_ids": ["attachment:456"], **provenance, "text": "Different replacement text"}]}, _context()
    ).business_result["records"][0]

    assert first["record_key"] == second["record_key"]
    assert {name: first[name] for name in provenance} == provenance


def test_missing_facts_are_null_and_marked_for_review():
    result = LLMStructuredExtract().execute(
        {"documents": [{"source_id": "mail-2", "text": "Invoice. Total: $40"}]}, _context()
    )

    record = result.business_result["records"][0]
    assert result.status == ResultState.REVIEW_REQUIRED
    assert record["amount"] == 40
    assert record["sender"] is None
    assert record["currency"] is None  # '$' is ambiguous across currencies.
    assert "sender" in record["missing_fields"]
    assert "currency" in record["missing_fields"]


def test_local_parser_marks_conflicting_amounts_and_currencies():
    result = LLMStructuredExtract().execute(
        {"documents": [{
            "source_id": "mail-conflict",
            "text": "Invoice\nSupplier: Example\nInvoice Number: INV-1\nTotal: USD 10\nTotal: EUR 11",
        }]},
        _context(),
    )

    record = result.business_result["records"][0]
    assert record["status"] == "REVIEW_REQUIRED"
    assert record["amount"] is None
    assert record["currency"] is None
    assert record["conflicts"] == ["amount", "currency"]


def test_conflicting_field_is_cleared_and_explicit():
    fixture = _extracted(invoice_number="INV-1", conflicts=["invoice_number"])
    result = LLMStructuredExtract().execute(
        {"documents": [{"source_id": "mail-3", "text": "invoice text", "mock_extraction": fixture}]}, _context()
    )

    record = result.business_result["records"][0]
    assert record["invoice_number"] is None
    assert record["conflicts"] == ["invoice_number"]
    assert record["status"] == "REVIEW_REQUIRED"


def test_replay_fixture_is_deterministic_and_does_not_call_provider():
    module = LLMStructuredExtract(api_key="unused", model="unused")
    result = module.execute(
        {"documents": [{"source_id": "replay-1", "text": "any", "replay_extraction": _extracted()}]},
        _context(RunMode.REPLAY),
    )

    assert result.status == ResultState.SUCCESS
    assert result.business_result["records"][0]["extraction_provider"] == "replay_fixture"


def test_real_mode_without_configuration_fails_without_network():
    result = LLMStructuredExtract(api_key="", model="").execute(
        {"documents": [{"source_id": "mail-4", "text": "invoice"}]}, _context(RunMode.REAL)
    )
    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "PROVIDER_NOT_CONFIGURED"


def test_real_mode_sends_strict_schema_and_parses_mocked_responses_http():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(_extracted())}]}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        module = LLMStructuredExtract(api_key="test-only-key", model="test-model", http_client=client)
        result = module.execute(
            {"documents": [{"source_id": "mail-5", "text": "invoice"}]}, _context(RunMode.REAL)
        )

    assert result.status == ResultState.SUCCESS
    request_body = json.loads(requests[0].content)
    schema_format = request_body["text"]["format"]
    assert schema_format["type"] == "json_schema"
    assert schema_format["strict"] is True
    assert schema_format["schema"]["additionalProperties"] is False
    assert "test-only-key" not in result.model_dump_json()


def test_real_mode_marks_malformed_or_unsupported_provider_output_invalid():
    responses = [
        {"output_text": "not-json"},
        {"output_text": json.dumps({**_extracted(), "surprise": True})},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        module = LLMStructuredExtract(api_key="test-only-key", model="test-model", http_client=client)
        for _ in range(2):
            result = module.execute(
                {"documents": [{"source_id": "mail-6", "text": "invoice"}]}, _context(RunMode.REAL)
            )
            assert result.errors[0].code == "INVALID_STRUCTURED_OUTPUT"
            record = result.business_result["records"][0]
            assert record["status"] == "REVIEW_REQUIRED"
            assert all(record[field] is None for field in ("sender", "invoice_number", "amount", "currency"))


def test_http_error_does_not_include_response_body_or_secret_in_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="sensitive provider detail")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = LLMStructuredExtract(
            api_key="secret-value", model="test-model", http_client=client
        ).execute({"documents": [{"source_id": "mail-7", "text": "invoice"}]}, _context(RunMode.REAL))

    serialized = result.model_dump_json()
    assert result.errors[0].retryability.value == "RETRYABLE"
    assert "secret-value" not in serialized
    assert "sensitive provider detail" not in serialized
