from __future__ import annotations

from workflow_automation.contracts import ExecutionContext, ResultState
from workflow_automation.modules.processing.validate import Validate


def _context() -> ExecutionContext:
    return ExecutionContext(workflow_id="test.invoice")


def _record(**overrides):
    record = {
        "record_key": "invoice:abc123",
        "document_type": "invoice",
        "sender": "Example Company",
        "invoice_number": "INV-1001",
        "amount": 1200.0,
        "currency": "USD",
        "source_ids": ["email-1/attachment-1"],
        "extraction_provider": "local_fixture",
        "extraction_model": "deterministic-v1",
        "status": "EXTRACTED",
        "confidence": None,
        "missing_fields": [],
        "conflicts": [],
    }
    record.update(overrides)
    return record


def test_valid_record_is_accepted_and_currency_normalized():
    provenance = {
        "source_system": "gmail",
        "source_account": "accounts/payables",
        "source_message_id": "message-1",
        "source_attachment_id": "attachment-1",
    }
    result = Validate().execute({"records": [_record(currency="usd", **provenance)]}, _context())

    assert result.status == ResultState.SUCCESS
    assert result.business_result["records"][0]["currency"] == "USD"
    assert {key: result.business_result["records"][0][key] for key in provenance} == provenance
    assert result.business_result["rejected"] == []
    assert Validate.module_id == "data.validate"


def test_missing_required_fields_are_rejected_with_stable_codes():
    record = _record(sender=None, invoice_number=None, amount=None, currency=None)
    result = Validate().execute({"records": [record]}, _context())

    errors = {item["code"] for item in result.business_result["rejected"][0]["errors"]}
    assert result.status == ResultState.REVIEW_REQUIRED
    assert {"MISSING_SENDER", "MISSING_INVOICE_NUMBER", "MISSING_AMOUNT", "MISSING_CURRENCY"} <= errors


def test_invalid_numeric_amounts_are_rejected():
    bad_values = ["not money", True, float("nan"), float("inf")]
    result = Validate().execute({"records": [_record(record_key=f"inv:{i}", amount=value) for i, value in enumerate(bad_values)]}, _context())

    assert len(result.business_result["rejected"]) == len(bad_values)
    assert all("INVALID_AMOUNT" in {item["code"] for item in rejected["errors"]} for rejected in result.business_result["rejected"])


def test_amount_thresholds_and_negative_amount_have_explicit_codes():
    validator = Validate(minimum_amount=0, maximum_amount=500)
    records = [
        _record(record_key="negative", amount=-1),
        _record(record_key="too-large", amount=501),
    ]
    result = validator.execute({"records": records}, _context())

    codes = [{item["code"] for item in record["errors"]} for record in result.business_result["rejected"]]
    assert "AMOUNT_BELOW_MINIMUM" in codes[0]
    assert "AMOUNT_ABOVE_MAXIMUM" in codes[1]


def test_currency_must_be_three_letters_and_optional_allowlist_is_enforced():
    validator = Validate(allowed_currencies={"USD", "EUR"})
    result = validator.execute(
        {"records": [
            _record(record_key="bad-format", currency="US"),
            _record(record_key="not-allowed", currency="JPY"),
        ]},
        _context(),
    )
    codes = [{item["code"] for item in record["errors"]} for record in result.business_result["rejected"]]
    assert "INVALID_CURRENCY" in codes[0]
    assert "UNSUPPORTED_CURRENCY" in codes[1]


def test_extraction_review_conflict_and_duplicate_keys_are_rejected():
    records = [
        _record(record_key="same"),
        _record(record_key="review", status="REVIEW_REQUIRED", conflicts=["amount"]),
        _record(record_key="same"),
    ]
    result = Validate().execute({"records": records}, _context())

    codes = [{item["code"] for item in record["errors"]} for record in result.business_result["rejected"]]
    assert result.status == ResultState.PARTIAL
    assert "EXTRACTION_REQUIRES_REVIEW" in codes[0]
    assert "DUPLICATE_RECORD_KEY" in codes[1]


def test_confidence_threshold_is_opt_in():
    record = _record(confidence=0.4)
    passing = Validate().execute({"records": [record]}, _context())
    failing = Validate(minimum_confidence=0.8).execute({"records": [record]}, _context())

    assert passing.status == ResultState.SUCCESS
    assert "CONFIDENCE_BELOW_MINIMUM" in {
        item["code"] for item in failing.business_result["rejected"][0]["errors"]
    }
