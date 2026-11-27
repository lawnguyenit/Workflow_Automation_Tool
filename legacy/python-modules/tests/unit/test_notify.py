import httpx

from workflow_automation.contracts import ExecutionContext, RunMode, ResultState
from workflow_automation.modules.output.notify import NotifySend


class FakeStore:
    def __init__(self):
        self.events = {}
        self.updated = []

    def enqueue_notification(self, key, destination, payload):
        if key in self.events:
            return {"id": "7", "created": False, "state": self.events[key]["state"]}
        self.events[key] = {"destination": destination, "payload": payload, "state": "PENDING"}
        return {"id": "7", "created": True, "state": "PENDING"}

    def update_notification(self, notification_id, state, error_code=None):
        self.updated.append((notification_id, state, error_code))
        for event in self.events.values():
            event["state"] = state
        return {"id": notification_id, "state": state}

    def claim_notification(self, notification_id):
        self.updated.append((notification_id, "CLAIMED", None))
        return True


def record():
    return {"record_id": "row-1", "record_key": "source-1", "invoice_number": "INV-1",
            "amount": "12.50", "currency": "USD", "sender": "private@example.invalid"}


def test_dry_run_does_not_create_outbox_or_contact_provider():
    store = FakeStore()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError())))
    module = NotifySend(store=store, webhook_url="https://hooks.slack.com/services/test", http_client=client)
    result = module.execute({"records": [record()]}, ExecutionContext(workflow_id="test", mode=RunMode.DRY_RUN))
    assert result.status == ResultState.SUCCESS
    assert result.business_result["notifications"][0]["status"] == "PREVIEWED"
    assert store.events == {}


def test_mock_persists_safe_intent_without_provider_call():
    store = FakeStore()
    module = NotifySend(store=store)
    result = module.execute({"records": [record()]}, ExecutionContext(workflow_id="test", mode=RunMode.MOCK))
    assert result.status == ResultState.SUCCESS
    assert result.business_result["notifications"][0]["status"] == "MOCKED"
    event = next(iter(store.events.values()))["payload"]
    assert "sender" not in event
    assert store.updated == [("7", "MOCKED", None)]


def test_unknown_slack_delivery_is_not_retried():
    store = FakeStore()
    def timeout(_request):
        raise httpx.ReadTimeout("timeout")
    client = httpx.Client(transport=httpx.MockTransport(timeout))
    module = NotifySend(store=store, webhook_url="https://hooks.slack.com/services/test", http_client=client)
    result = module.execute({"records": [record()]}, ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert result.status == ResultState.REVIEW_REQUIRED
    assert result.errors[0].code == "DELIVERY_TIMEOUT_UNKNOWN"
    assert store.updated == [("7", "CLAIMED", None), ("7", "UNKNOWN", "DELIVERY_TIMEOUT")]


def test_lost_outbox_claim_prevents_provider_send():
    store = FakeStore()
    store.claim_notification = lambda _notification_id: False
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(AssertionError())))
    module = NotifySend(store=store, webhook_url="https://hooks.slack.com/services/test", http_client=client)
    result = module.execute({"records": [record()]}, ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert result.status == ResultState.REVIEW_REQUIRED
    assert result.errors[0].code == "DELIVERY_ALREADY_CLAIMED"


def test_rate_limited_intent_is_not_marked_retryable_without_a_requeue_path():
    store = FakeStore()
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(429, headers={"Retry-After": "30"})))
    module = NotifySend(store=store, webhook_url="https://hooks.slack.com/services/test", http_client=client)
    result = module.execute({"records": [record()]}, ExecutionContext(workflow_id="test", mode=RunMode.REAL))
    assert result.status == ResultState.FAILURE
    assert result.errors[0].code == "SLACK_REJECTED"
    assert result.errors[0].retryability.value == "NOT_RETRYABLE"
    assert store.updated[-1] == ("7", "FAILED", "SLACK_REJECTED")
