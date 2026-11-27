from __future__ import annotations

import pytest

from workflow_automation.contracts import ExecutionContext, ModuleResult, ResultState, RunMode
from workflow_automation.executors import PythonExecutor
from workflow_automation.models import (
    ExecutorDefinition,
    ModuleDefinition,
    ModuleImplementation,
    ModuleInstance,
    WorkflowDefinition,
    WorkflowInstance,
)
from workflow_automation.registry import FrameworkRegistry
from workflow_automation.runner import WorkflowRunner


class EchoModule:
    module_id = "test.echo"

    def execute(self, payload, context):
        return ModuleResult(module_id=self.module_id, status=ResultState.SUCCESS,
                            business_result={"value": payload["value"] + 1}, context=context).complete()


class ResultModule:
    def __init__(self, module_id, status, calls):
        self.module_id = module_id
        self.status = status
        self.calls = calls

    def execute(self, payload, context):
        self.calls.append(self.module_id)
        return ModuleResult(module_id=self.module_id, status=self.status,
                            business_result={"value": payload.get("value", 7)},
                            context=context).complete()


def registry_with_echo() -> FrameworkRegistry:
    registry = FrameworkRegistry()
    registry.add_definition(ModuleDefinition(module_id="test.echo", purpose="test",
                                             input_schema={"type": "object"},
                                             output_schema={"type": "object"},
                                             possible_results=["SUCCESS", "FAILURE"]))
    registry.add_implementation(ModuleImplementation(implementation_id="test.echo.python",
                                                     implements="test.echo", runtime="python"))
    registry.add_executor(ExecutorDefinition(executor_id="python.local", runtime="python"))
    registry.bind_executor("python.local", PythonExecutor())
    registry.add_runtime_module("test.echo.python", EchoModule())
    registry.add_instance(ModuleInstance(instance_id="echo", module="test.echo",
                                          implementation="test.echo.python", executor_id="python.local"))
    registry.add_workflow(WorkflowDefinition(workflow_id="test.workflow", description="test", steps=[
        {"step_id": "first", "module_id": "test.echo", "input_mapping": {"value": "$input.value"}},
        {"step_id": "second", "module_id": "test.echo", "depends_on": ["first"],
         "input_mapping": {"value": "first.value"}},
    ]))
    registry.add_workflow_instance(WorkflowInstance(
        instance_id="test.workflow.instance", workflow_definition_id="test.workflow",
        step_bindings={"first": "echo", "second": "echo"},
    ))
    return registry


def test_runner_maps_input_and_result_through_shared_contract():
    registry = registry_with_echo()
    result = WorkflowRunner(registry).run("test.workflow.instance", {"value": 1},
                                         ExecutionContext(workflow_id="test.workflow", mode=RunMode.MOCK))

    assert result["status"] == "SUCCESS"
    assert result["result"] == {"value": 3}
    assert result["steps"]["first"]["business_result"] == {"value": 2}


def test_registry_rejects_mapping_without_declared_dependency():
    registry = registry_with_echo()
    with pytest.raises(ValueError, match="must declare"):
        registry.add_workflow(WorkflowDefinition(workflow_id="invalid.mapping", description="test", steps=[
            {"step_id": "first", "module_id": "test.echo", "input_mapping": {"value": "$input.value"}},
            {"step_id": "second", "module_id": "test.echo", "input_mapping": {"value": "first.value"}},
        ]))


def test_registry_rejects_cycles():
    registry = registry_with_echo()
    with pytest.raises(ValueError, match="cycle"):
        registry.add_workflow(WorkflowDefinition(workflow_id="invalid.cycle", description="test", steps=[
            {"step_id": "first", "module_id": "test.echo", "depends_on": ["second"]},
            {"step_id": "second", "module_id": "test.echo", "depends_on": ["first"]},
        ]))


def registry_with_review_gate(*, qualification_status=ResultState.PARTIAL):
    registry = FrameworkRegistry()
    registry.add_executor(ExecutorDefinition(executor_id="python.local", runtime="python"))
    registry.bind_executor("python.local", PythonExecutor())
    calls = []
    modules = [
        ("source", ResultState.SUCCESS),
        ("persist", ResultState.SUCCESS),
        ("qualify", qualification_status),
        ("crm", ResultState.SUCCESS),
    ]
    bindings = {}
    for name, status in modules:
        capability = f"test.{name}"
        implementation = f"{capability}.python"
        instance_id = name
        registry.add_definition(ModuleDefinition(
            module_id=capability, purpose="test", input_schema={"type": "object"},
            output_schema={"type": "object"}, possible_results=["SUCCESS", "PARTIAL"],
        ))
        registry.add_implementation(ModuleImplementation(
            implementation_id=implementation, implements=capability, runtime="python",
        ))
        registry.add_instance(ModuleInstance(
            instance_id=instance_id, module=capability, implementation=implementation,
            executor_id="python.local",
        ))
        registry.bind_instance_module(instance_id, ResultModule(capability, status, calls))
        bindings[name] = instance_id

    registry.add_workflow(WorkflowDefinition(workflow_id="lead.gated", description="test", steps=[
        {"step_id": "source", "module_id": "test.source"},
        {"step_id": "persist", "module_id": "test.persist", "depends_on": ["source"],
         "continue_on_partial": True, "input_mapping": {"value": "source.value"}},
        {"step_id": "qualify", "module_id": "test.qualify", "depends_on": ["persist"],
         "continue_on_partial": True, "input_mapping": {"value": "persist.value"}},
        {"step_id": "crm", "module_id": "test.crm", "depends_on": ["persist", "qualify"],
         "requires_success_of": ["qualify"], "input_mapping": {"value": "persist.value"}},
    ]))
    registry.add_workflow_instance(WorkflowInstance(
        instance_id="lead.gated.instance", workflow_definition_id="lead.gated",
        step_bindings=bindings,
    ))
    return registry, calls


def test_runner_persists_review_path_but_gates_downstream_side_effects():
    registry, calls = registry_with_review_gate()
    result = WorkflowRunner(registry).run(
        "lead.gated.instance", {}, ExecutionContext(workflow_id="lead.gated", mode=RunMode.MOCK),
    )

    assert result["status"] == "PARTIAL"
    assert result["steps"]["persist"]["status"] == "SUCCESS"
    assert result["skipped_steps"] == ["crm"]
    assert result["gated_steps"] == {"crm": ["qualify"]}
    assert calls == ["test.source", "test.persist", "test.qualify"]


def test_runner_invokes_downstream_step_after_success_requirement_passes():
    registry, calls = registry_with_review_gate(qualification_status=ResultState.SUCCESS)
    result = WorkflowRunner(registry).run(
        "lead.gated.instance", {}, ExecutionContext(workflow_id="lead.gated", mode=RunMode.MOCK),
    )

    assert result["status"] == "SUCCESS"
    assert result["gated_steps"] == {}
    assert "test.crm" in calls


def test_registry_requires_success_condition_to_reference_a_dependency():
    registry = registry_with_echo()
    with pytest.raises(ValueError, match="must depend on"):
        registry.add_workflow(WorkflowDefinition(workflow_id="invalid.gate", description="test", steps=[
            {"step_id": "first", "module_id": "test.echo"},
            {"step_id": "second", "module_id": "test.echo", "requires_success_of": ["first"]},
        ]))


def test_registry_requires_complete_workflow_bindings_and_matching_capabilities():
    from workflow_automation.models import WorkflowInstance

    registry = registry_with_echo()
    with pytest.raises(ValueError, match="bind every workflow step"):
        registry.add_workflow_instance(WorkflowInstance(instance_id="incomplete",
            workflow_definition_id="test.workflow", step_bindings={"first": "echo"}))

    registry.add_definition(ModuleDefinition(module_id="other.capability", purpose="test",
        input_schema={"type": "object"}, output_schema={"type": "object"}, possible_results=["SUCCESS"]))
    registry.add_implementation(ModuleImplementation(implementation_id="other.python",
        implements="other.capability", runtime="python"))
    registry.add_instance(ModuleInstance(instance_id="other", module="other.capability",
        implementation="other.python", executor_id="python.local"))
    with pytest.raises(ValueError, match="does not match"):
        registry.add_workflow_instance(WorkflowInstance(instance_id="mismatch",
            workflow_definition_id="test.workflow", step_bindings={"first": "other", "second": "echo"}))
