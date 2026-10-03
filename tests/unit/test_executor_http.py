import json

import httpx

from workflow_automation.contracts import ExecutionContext, ModuleResult, ResultState
from workflow_automation.executors import HttpApiExecutor


class EchoImplementation:
    module_id = "test.echo"


def test_http_executor_targets_bound_instance_and_carries_context_and_auth():
    observed = []

    def respond(request):
        observed.append(request)
        context = ExecutionContext.model_validate(json.loads(request.content)["context"])
        return httpx.Response(200, json=ModuleResult(module_id="test.echo", status=ResultState.SUCCESS,
            context=context).model_dump(mode="json"))

    client = httpx.Client(base_url="https://modules.example.invalid", transport=httpx.MockTransport(respond))
    executor = HttpApiExecutor("https://modules.example.invalid", api_key="test-key", client=client)
    context = ExecutionContext(workflow_id="test.workflow")
    result = executor.invoke(EchoImplementation(), {"value": 1}, context, instance_id="tenant.echo")
    assert result.status == ResultState.SUCCESS
    assert observed[0].url.path == "/v1/instances/tenant.echo/runs"
    assert observed[0].headers["Authorization"] == "Bearer test-key"


def test_http_executor_requires_concrete_instance_id():
    executor = HttpApiExecutor("https://modules.example.invalid")
    try:
        executor.invoke(EchoImplementation(), {}, ExecutionContext(workflow_id="test"))
    except ValueError as exc:
        assert "instance ID" in str(exc)
    else:
        raise AssertionError("expected a clear error when the instance ID is missing")
