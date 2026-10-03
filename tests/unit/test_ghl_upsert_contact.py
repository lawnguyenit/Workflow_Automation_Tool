import httpx

from workflow_automation.contracts import ExecutionContext, RunMode, ResultState
from workflow_automation.infrastructure.postgres_ghl_contact_upsert_ledger import (
    GHLContactInputConflict,
    GHLContactReconciliationRequired,
)
from workflow_automation.modules.crm.ghl_upsert_contact import GHLUpsertContact


def _context(mode=RunMode.REAL):
    return ExecutionContext(workflow_id="ghl_test", mode=mode)


class _MemoryContactLedger:
    def __init__(self):
        self.events = {}

    def run_once(self, *, event_key, input_fingerprint, operation, uncertain_error_code):
        previous = self.events.get(event_key)
        if previous is not None:
            if previous[0] != input_fingerprint:
                raise GHLContactInputConflict()
            if previous[1] == "UNKNOWN":
                raise GHLContactReconciliationRequired()
            return {"state": "UPSERTED", "contact_id": previous[2]["contact_id"],
                    "new": previous[2]["new"], "side_effect": False, "cached": True}, True
        result = operation()
        error_code = uncertain_error_code(result)
        if error_code:
            self.events[event_key] = (input_fingerprint, "UNKNOWN", None)
        elif result.status == ResultState.SUCCESS:
            saved = {"contact_id": result.business_result["contact_id"], "new": result.business_result["new"]}
            self.events[event_key] = (input_fingerprint, "SUCCEEDED", saved)
        return result, False


def _module(handler, *, token="test-token"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GHLUpsertContact(location_id="test-location", bearer_token=token, http_client=client,
                            effect_ledger=_MemoryContactLedger()), client


def _payload(**contact):
    return {"idempotency_key": "forms:test-account:response-123",
            "contact": {"email": "ada@example.invalid", "name": "Ada Example", **contact}}


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


def test_contact_upsert_cannot_opt_out_of_duplicate_matching():
    module = GHLUpsertContact(location_id="test-location", bearer_token="test-token",
                              effect_ledger=_MemoryContactLedger())
    result = module.execute(_payload(createNewIfDuplicateAllowed=True), _context())
    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "INVALID_CONTACT"
    assert "bypassing duplicate matching" in result.errors[0].message


def test_timeout_is_durably_held_and_same_event_replay_does_not_call_provider_again():
    calls = []

    def handler(_request):
        calls.append(True)
        raise httpx.ReadTimeout("possibly sensitive transport detail")

    module, client = _module(handler)
    try:
        result = module.execute(_payload(), _context())
        replay = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status.value == "REVIEW_REQUIRED"
    assert result.errors[0].code == "REMOTE_OUTCOME_UNKNOWN"
    assert result.errors[0].retryability.value == "UNKNOWN"
    assert "possibly sensitive" not in str(result.model_dump())
    assert replay.errors[0].code == "CONTACT_RECONCILIATION_REQUIRED"
    assert calls == [True]


def test_confirmed_success_replay_returns_cached_contact_without_another_provider_call():
    calls = []

    def handler(_request):
        calls.append(True)
        return httpx.Response(200, json={"new": True, "contact": {"id": "contact-123"}})

    module, client = _module(handler)
    try:
        first = module.execute(_payload(), _context())
        replay = module.execute(_payload(), _context())
        changed = module.execute(_payload(name="Changed Name"), _context())
    finally:
        client.close()
    assert first.business_result["side_effect"] is True
    assert replay.business_result == {"state": "UPSERTED", "contact_id": "contact-123", "new": True,
                                     "side_effect": False, "cached": True}
    assert changed.errors[0].code == "CONTACT_EVENT_PAYLOAD_CONFLICT"
    assert len(calls) == 1


def test_real_contact_write_requires_source_event_key_and_effect_guard():
    missing_key = {"contact": {"email": "ada@example.invalid"}}
    module, client = _module(lambda _request: httpx.Response(200, json={}))
    try:
        result = module.execute(missing_key, _context())
    finally:
        client.close()
    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "INVALID_CONTACT"

    unconfigured = GHLUpsertContact(location_id="test-location", bearer_token="test-token",
                                    effect_ledger=None)
    result = unconfigured.execute(_payload(), _context())
    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "CONTACT_LEDGER_NOT_READY"


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

    missing_identifier = GHLUpsertContact(location_id="test-location", bearer_token="test-token",
                                          effect_ledger=_MemoryContactLedger())
    result = missing_identifier.execute({"idempotency_key": "source:account:event", "contact": {"name": "Ada"}},
                                        _context())
    assert result.errors[0].code == "INVALID_CONTACT"


def test_contact_mapping_rejects_oversized_provider_payload():
    module = GHLUpsertContact(location_id="test-location", bearer_token="test-token")
    result = module.execute(_payload(customFields=[{"id": "field-1", "fieldValue": "A" * 33_000}]),
                            _context())
    assert result.status.value == "FAILURE"
    assert result.errors[0].code == "INVALID_CONTACT"
    assert "32 KiB" in result.errors[0].message
