import httpx

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.crm.ghl_upsert_contact import GHLUpsertContact


def _context(mode=RunMode.REAL):
    return ExecutionContext(workflow_id="ghl_test", mode=mode)


def _module(handler, *, token="test-token"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GHLUpsertContact(location_id="test-location", bearer_token=token, http_client=client), client


def _payload(**contact):
    return {"contact": {"email": "ada@example.invalid", "name": "Ada Example", **contact}}


def test_real_upsert_sends_scoped_v3_request_and_returns_no_contact_pii():
    observed = {}

    def handler(request):
        observed["url"] = str(request.url)
        observed["headers"] = request.headers
        observed["body"] = request.read().decode()
        return httpx.Response(200, json={"new": False, "contact": {"id": "contact-123",
                                                                          "email": "ada@example.invalid"}})

    module, client = _module(handler)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()

    assert observed["url"] == "https://services.leadconnectorhq.com/contacts/upsert"
    assert observed["headers"]["Authorization"] == "Bearer test-token"
    assert observed["headers"]["Version"] == "v3"
    assert '"locationId":"test-location"' in observed["body"]
    assert "tags" not in observed["body"]
    assert result.business_result == {"state": "UPSERTED", "contact_id": "contact-123",
                                      "new": False, "side_effect": True}
    assert "ada@example.invalid" not in str(result.model_dump())


def test_dry_run_and_mock_never_call_provider():
    calls = []
    module, client = _module(lambda _request: calls.append(True) or httpx.Response(200))
    try:
        dry_run = module.execute(_payload(), _context(RunMode.DRY_RUN))
        mock = module.execute(_payload(), _context(RunMode.MOCK))
    finally:
        client.close()
    assert dry_run.business_result["side_effect"] is False
    assert mock.business_result["side_effect"] is False
    assert calls == []


def test_tags_are_rejected_to_avoid_replacing_existing_contact_tags():
    module = GHLUpsertContact(location_id="test-location", bearer_token="test-token")
    result = module.execute(_payload(tags=["new-tag"]), _context())
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "INVALID_CONTACT"
    assert "replaces the full tag list" in result.errors[0].message


def test_timeout_is_unknown_and_blocks_automatic_replay():
    def handler(_request):
        raise httpx.ReadTimeout("possibly sensitive transport detail")

    module, client = _module(handler)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status.value == "REVIEW_REQUIRED"
    assert result.errors[0].code == "REMOTE_OUTCOME_UNKNOWN"
    assert result.errors[0].retryability.value == "UNKNOWN"
    assert "possibly sensitive" not in str(result.model_dump())


def test_rate_limit_returns_bounded_retry_hint_without_automatic_retry():
    module, client = _module(lambda _request: httpx.Response(429, headers={"Retry-After": "3"}))
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status.value == "REVIEW_REQUIRED"
    assert result.errors[0].retryability.value == "RETRYABLE"
    assert result.metrics["retry_after_seconds"] == 3


def test_auth_failure_is_non_retryable_and_server_error_is_uncertain():
    for status, expected_state, expected_code, expected_retryability in [
        (403, "FAILURE", "AUTHORIZATION_FAILED", "NOT_RETRYABLE"),
        (500, "REVIEW_REQUIRED", "REMOTE_OUTCOME_UNKNOWN", "UNKNOWN"),
    ]:
        module, client = _module(lambda _request, status=status: httpx.Response(status))
        try:
            result = module.execute(_payload(), _context())
        finally:
            client.close()
        assert result.status.value == expected_state
        assert result.errors[0].code == expected_code
        assert result.errors[0].retryability.value == expected_retryability


def test_contact_must_have_email_or_phone_and_runtime_token_is_required():
    module = GHLUpsertContact(location_id="test-location", bearer_token="test-token")
    result = module.execute({"contact": {"name": "Ada"}}, _context())
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "INVALID_CONTACT"

    missing_token = GHLUpsertContact(location_id="test-location", bearer_token="")
    result = missing_token.execute(_payload(), _context())
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "CREDENTIAL_NOT_CONFIGURED"


def test_contact_mapping_rejects_oversized_provider_payload():
    module = GHLUpsertContact(location_id="test-location", bearer_token="test-token")
    result = module.execute(_payload(customFields=[{"id": "field-1", "fieldValue": "A" * 33_000}]),
                            _context())
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "INVALID_CONTACT"
    assert "32 KiB" in result.errors[0].message
