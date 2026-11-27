import httpx

from workflow_automation.contracts import ExecutionContext, RunMode
from workflow_automation.modules.crm.ghl_contact_tags import GHLContactTags


class DirectGuard:
    def __init__(self):
        self.effect_key = None
        self.error = None

    def run(self, *, effect_key, operation, uncertain_error_code, **_kwargs):
        self.effect_key = effect_key
        result = operation()
        self.error = uncertain_error_code(result)
        return result


def _context(mode=RunMode.REAL):
    return ExecutionContext(workflow_id="ghl_tag_test", mode=mode)


def test_real_add_tags_uses_add_only_endpoint_and_redacts_provider_tag_list():
    observed = {}

    def handler(request):
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["headers"] = request.headers
        observed["body"] = request.read()
        return httpx.Response(201, json={"tags": ["existing", "qualified"]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    guard = DirectGuard()
    module = GHLContactTags(location_id="synthetic-location", bearer_token="test-token",
                            http_client=client, effect_guard=guard)
    try:
        result = module.execute({"contact_id": "contact_123", "tags": [" qualified "]}, _context())
    finally:
        client.close()
    assert result.status.value == "SUCCESS"
    assert result.business_result == {"state": "TAGS_ADDED", "side_effect": True}
    assert "existing" not in str(result.model_dump())
    assert observed["method"] == "POST"
    assert observed["url"] == "https://services.leadconnectorhq.com/contacts/contact_123/tags"
    assert observed["headers"]["Authorization"] == "Bearer test-token"
    assert observed["headers"]["Version"] == "v3"
    assert observed["body"] == b'{"tags":["qualified"]}'
    assert len(guard.effect_key) == 64
    assert guard.error is None


def test_preview_and_mock_modes_never_call_highlevel_or_guard():
    def forbidden(_request):
        raise AssertionError("no provider request allowed")

    client = httpx.Client(transport=httpx.MockTransport(forbidden))
    module = GHLContactTags(location_id="synthetic-location", bearer_token="test-token",
                            http_client=client, effect_guard=None)
    try:
        preview = module.execute({"contact_id": "contact_123", "tags": ["qualified"]},
                                 _context(RunMode.DRY_RUN))
        mock = module.execute({"contact_id": "contact_123", "tags": ["qualified"]},
                              _context(RunMode.MOCK))
    finally:
        client.close()
    assert preview.business_result == {"state": "PREVIEWED", "side_effect": False}
    assert mock.business_result == {"state": "MOCKED", "side_effect": False}


def test_validation_and_guard_are_required_before_real_write():
    module = GHLContactTags(location_id="synthetic-location", bearer_token="test-token")
    invalid = module.execute({"contact_id": "../contact", "tags": ["x", "x"]}, _context())
    unguarded = module.execute({"contact_id": "contact_123", "tags": ["qualified"]}, _context())
    extra_field = module.execute({"contact_id": "contact_123", "tags": ["qualified"], "location_id": "other"},
                                 _context())
    assert invalid.errors[0].code == "INVALID_CONTACT_TAGS"
    assert unguarded.errors[0].code == "EFFECT_GUARD_NOT_CONFIGURED"
    assert extra_field.errors[0].code == "INVALID_CONTACT_TAGS"


def test_auth_rate_limit_and_unknown_provider_outcomes_are_classified_without_body_echo():
    cases = [(403, {}, "AUTHORIZATION_FAILED"),
             (429, {"Retry-After": "7"}, "RATE_LIMITED"),
             (502, {}, "REMOTE_OUTCOME_UNKNOWN"),
             (201, {}, "REMOTE_RESPONSE_UNVERIFIED")]
    for status, headers, expected in cases:
        client = httpx.Client(transport=httpx.MockTransport(
            lambda _request, status=status, headers=headers: httpx.Response(
                status, headers=headers, json={"private": "provider response"}
            )
        ))
        module = GHLContactTags(location_id="synthetic-location", bearer_token="test-token",
                                http_client=client, effect_guard=DirectGuard())
        try:
            result = module.execute({"contact_id": "contact_123", "tags": ["qualified"]}, _context())
        finally:
            client.close()
        assert result.errors[0].code == expected
        assert "provider response" not in str(result.model_dump())
        if status == 429:
            assert result.metrics["retry_after_seconds"] == 7
        if status == 502 or status == 201:
            assert result.status.value == "REVIEW_REQUIRED"
