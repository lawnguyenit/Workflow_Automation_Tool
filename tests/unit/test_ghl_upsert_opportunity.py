import json

import httpx

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.infrastructure.postgres_effect_guard import EffectReconciliationPending
from workflow_automation.modules.crm.ghl_upsert_opportunity import GHLUpsertOpportunity


def _context(mode=RunMode.REAL):
    return ExecutionContext(workflow_id="ghl_opportunity_test", mode=mode)


def _payload(**changes):
    opportunity = {
        "contactId": "contact-123",
        "pipelineId": "pipeline-456",
        "pipelineStageId": "stage-789",
        "name": "Ada Example - Workflow Automation",
        "status": "open",
        "monetaryValue": 2500,
        "forecastProbability": 35,
        "customFields": [{"id": "field-1", "fieldValue": "qualified"}],
    }
    return {"opportunity": {**opportunity, **changes}}


class _FakeEffectGuard:
    def __init__(self):
        self.pending = set()
        self.keys = []

    def run(self, *, effect_key, operation, uncertain_error_code, **_kwargs):
        self.keys.append(effect_key)
        if effect_key in self.pending:
            raise EffectReconciliationPending()
        result = operation()
        if uncertain_error_code(result):
            self.pending.add(effect_key)
        return result


def _module(handler, *, token="test-token", guard=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GHLUpsertOpportunity(location_id="test-location", bearer_token=token,
                                http_client=client, effect_guard=guard or _FakeEffectGuard()), client


def test_creates_when_no_open_contact_pipeline_match_and_suppresses_pii():
    observed = []

    def handler(request):
        observed.append(request)
        if request.url.path.endswith("/opportunities/search"):
            assert request.url.params["locationId"] == "test-location"
            assert request.url.params["pipelineId"] == "pipeline-456"
            assert request.url.params["contactId"] == "contact-123"
            assert request.url.params["status"] == "open"
            assert request.headers["Authorization"] == "Bearer test-token"
            assert request.headers["Version"] == "v3"
            return httpx.Response(200, json={"opportunities": [], "meta": {"total": 0}})
        assert request.method == "POST"
        assert request.url.path == "/opportunities/"
        body = json.loads(request.read())
        assert body["locationId"] == "test-location"
        assert body["contactId"] == "contact-123"
        assert body["customFields"] == [{"id": "field-1", "fieldValue": "qualified"}]
        return httpx.Response(201, json={"opportunity": {"id": "opp-123", "name": "Private name"}})

    module, client = _module(handler)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "SUCCESS"
    assert result.business_result == {"state": "UPSERTED", "opportunity_id": "opp-123",
                                     "new": True, "side_effect": True}
    assert "Ada Example" not in str(result.model_dump())
    assert len(observed) == 2


def test_updates_the_only_open_match_without_sending_contact_id_to_update():
    observed = []

    def handler(request):
        observed.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"opportunities": [{"id": "existing-opp"}], "meta": {"total": 1}})
        assert request.method == "PUT"
        assert request.url.path.endswith("/opportunities/existing-opp")
        assert "contactId" not in json.loads(request.read())
        return httpx.Response(200, json={"opportunity": {"id": "existing-opp"}})

    module, client = _module(handler)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.business_result["new"] is False
    assert result.business_result["opportunity_id"] == "existing-opp"
    assert [request.method for request in observed] == ["GET", "PUT"]


def test_ambiguous_open_matches_require_review_without_writing():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"opportunities": [{"id": "opp-a"}, {"id": "opp-b"}]})

    module, client = _module(handler)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "REVIEW_REQUIRED"
    assert result.errors[0].code == "AMBIGUOUS_OPEN_OPPORTUNITY"
    assert calls == ["GET"]


def test_search_paginates_before_refusing_ambiguous_matches():
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        items = [{"id": f"opp-{index}"} for index in range(100)] if page == 1 else []
        return httpx.Response(200, json={"opportunities": items})

    module, client = _module(handler)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "REVIEW_REQUIRED"
    assert result.errors[0].code == "AMBIGUOUS_OPEN_OPPORTUNITY"
    assert pages == [1, 2]


def test_dry_run_and_mock_never_call_provider():
    calls = []
    module, client = _module(lambda request: calls.append(request) or httpx.Response(200))
    try:
        dry_run = module.execute(_payload(), _context(RunMode.DRY_RUN))
        mock = module.execute(_payload(), _context(RunMode.MOCK))
    finally:
        client.close()
    assert dry_run.business_result == {"state": "PREVIEWED", "side_effect": False}
    assert mock.business_result["side_effect"] is False
    assert calls == []


def test_search_timeout_is_safe_to_retry_but_write_timeout_is_ambiguous():
    def timeout_on_search(_request):
        raise httpx.ReadTimeout("search timeout")

    module, client = _module(timeout_on_search)
    try:
        search_result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert search_result.errors[0].retryability.value == "RETRYABLE"
    assert search_result.errors[0].code == "SEARCH_UNAVAILABLE"

    def timeout_on_write(request):
        if request.method == "GET":
            return httpx.Response(200, json={"opportunities": []})
        raise httpx.ReadTimeout("write timeout")

    module, client = _module(timeout_on_write)
    try:
        write_result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert write_result.status == "REVIEW_REQUIRED"
    assert write_result.errors[0].retryability.value == "UNKNOWN"
    assert write_result.errors[0].code == "REMOTE_OUTCOME_UNKNOWN"


def test_unknown_write_is_durably_held_by_guard_and_retry_makes_no_provider_call():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"opportunities": []})
        raise httpx.ReadTimeout("write timeout")

    guard = _FakeEffectGuard()
    module, client = _module(handler, guard=guard)
    try:
        first = module.execute(_payload(), _context())
        second = module.execute(_payload(), _context())
    finally:
        client.close()
    assert first.errors[0].code == "REMOTE_OUTCOME_UNKNOWN"
    assert first.metrics["effect_key"] == second.metrics["effect_key"]
    assert len(first.metrics["effect_key"]) == 64
    assert "contact-123" not in first.metrics["effect_key"]
    assert second.errors[0].code == "EFFECT_RECONCILIATION_REQUIRED"
    assert calls == ["GET", "POST"]


def test_auth_scope_and_provider_server_errors_are_distinguished():
    module, client = _module(lambda _request: httpx.Response(403))
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.errors[0].code == "AUTHORIZATION_FAILED"
    assert result.errors[0].retryability.value == "NOT_RETRYABLE"

    def server_error_after_write(request):
        if request.method == "GET":
            return httpx.Response(200, json={"opportunities": []})
        return httpx.Response(503)

    module, client = _module(server_error_after_write)
    try:
        result = module.execute(_payload(), _context())
    finally:
        client.close()
    assert result.status == "REVIEW_REQUIRED"
    assert result.errors[0].retryability.value == "UNKNOWN"


def test_missing_credentials_429_and_malformed_provider_responses_are_bounded():
    calls = []
    module, client = _module(lambda request: calls.append(request) or httpx.Response(200), token="")
    try:
        missing_token = module.execute(_payload(), _context())
    finally:
        client.close()
    assert missing_token.errors[0].code == "CREDENTIAL_NOT_CONFIGURED"
    assert calls == []

    module, client = _module(lambda _request: httpx.Response(200, json={"unexpected": []}))
    try:
        malformed = module.execute(_payload(), _context())
    finally:
        client.close()
    assert malformed.status == "REVIEW_REQUIRED"
    assert malformed.errors[0].code == "SEARCH_RESPONSE_UNVERIFIED"

    module, client = _module(lambda _request: httpx.Response(429, headers={"Retry-After": "5"}))
    try:
        rate_limited = module.execute(_payload(), _context())
    finally:
        client.close()
    assert rate_limited.errors[0].code == "RATE_LIMITED"
    assert rate_limited.errors[0].retryability.value == "RETRYABLE"


def test_invalid_and_oversized_mapping_is_rejected_before_network():
    calls = []
    module, client = _module(lambda request: calls.append(request) or httpx.Response(200))
    try:
        invalid = module.execute(_payload(forecastProbability=101), _context())
        oversized = module.execute(_payload(name="A" * 33_000), _context())
        closed_status = module.execute(_payload(status="won"), _context())
        invalid_date = module.execute(_payload(forecastExpectedCloseDate="next Friday"), _context())
    finally:
        client.close()
    assert invalid.errors[0].code == "INVALID_OPPORTUNITY"
    assert oversized.errors[0].code == "INVALID_OPPORTUNITY"
    assert closed_status.errors[0].code == "INVALID_OPPORTUNITY"
    assert invalid_date.errors[0].code == "INVALID_OPPORTUNITY"
    assert calls == []
