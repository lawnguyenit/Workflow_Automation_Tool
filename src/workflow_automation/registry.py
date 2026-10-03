from __future__ import annotations

from typing import Any

from workflow_automation.models import (
    ExecutorDefinition,
    ModuleDefinition,
    ModuleImplementation,
    ModuleInstance,
    WorkflowDefinition,
    WorkflowInstance,
)


class FrameworkRegistry:
    """Validated metadata registries for capabilities, implementations, and instances."""

    def __init__(self) -> None:
        self.definitions: dict[str, ModuleDefinition] = {}
        self.implementations: dict[str, ModuleImplementation] = {}
        self.executors: dict[str, ExecutorDefinition] = {}
        self.executor_bindings: dict[str, Any] = {}
        self.instances: dict[str, ModuleInstance] = {}
        self.workflow_definitions: dict[str, WorkflowDefinition] = {}
        self.workflow_instances: dict[str, WorkflowInstance] = {}
        self.runtime_modules: dict[str, Any] = {}
        self.instance_modules: dict[str, Any] = {}

    def add_definition(self, item: ModuleDefinition) -> None:
        if item.module_id in self.definitions:
            raise ValueError(f"Duplicate module definition: {item.module_id}")
        self.definitions[item.module_id] = item

    def add_implementation(self, item: ModuleImplementation) -> None:
        if item.implementation_id in self.implementations:
            raise ValueError(f"Duplicate module implementation: {item.implementation_id}")
        if item.implements not in self.definitions:
            raise ValueError(f"Unknown capability {item.implements!r} for {item.implementation_id}")
        self.implementations[item.implementation_id] = item

    def add_executor(self, item: ExecutorDefinition) -> None:
        if item.executor_id in self.executors:
            raise ValueError(f"Duplicate executor: {item.executor_id}")
        self.executors[item.executor_id] = item

    def bind_executor(self, executor_id: str, executor: Any) -> None:
        if executor_id not in self.executors:
            raise ValueError(f"Unknown executor: {executor_id}")
        self.executor_bindings[executor_id] = executor

    def add_runtime_module(self, implementation_id: str, module: Any) -> None:
        if implementation_id not in self.implementations:
            raise ValueError(f"Unknown implementation: {implementation_id}")
        if implementation_id in self.runtime_modules:
            raise ValueError(f"Runtime implementation already registered: {implementation_id}")
        self.runtime_modules[implementation_id] = module

    def add_instance(self, item: ModuleInstance) -> None:
        if item.instance_id in self.instances:
            raise ValueError(f"Duplicate module instance: {item.instance_id}")
        if item.module not in self.definitions:
            raise ValueError(f"Unknown capability: {item.module}")
        implementation = self.implementations.get(item.implementation)
        if implementation is None:
            raise ValueError(f"Unknown implementation: {item.implementation}")
        if implementation.implements != item.module:
            raise ValueError("Instance capability and implementation do not match")
        if item.executor_id not in self.executors:
            raise ValueError(f"Unknown executor: {item.executor_id}")
        self.instances[item.instance_id] = item
        if item.implementation in self.runtime_modules:
            self.instance_modules[item.instance_id] = self.runtime_modules[item.implementation]

    def bind_instance_module(self, instance_id: str, module: Any) -> None:
        if instance_id not in self.instances:
            raise ValueError(f"Unknown module instance: {instance_id}")
        self.instance_modules[instance_id] = module

    def add_workflow(self, item: WorkflowDefinition) -> None:
        if item.workflow_id in self.workflow_definitions:
            raise ValueError(f"Duplicate workflow: {item.workflow_id}")
        step_ids = {step.step_id for step in item.steps}
        if len(step_ids) != len(item.steps):
            raise ValueError("Workflow step IDs must be unique")
        for step in item.steps:
            if step.module_id not in self.definitions:
                raise ValueError(f"Unknown module capability for step {step.step_id}: {step.module_id}")
            for dependency in step.depends_on:
                if dependency not in step_ids:
                    raise ValueError(f"Unknown dependency {dependency!r} in step {step.step_id}")
                if dependency == step.step_id:
                    raise ValueError(f"Workflow step cannot depend on itself: {step.step_id}")
            if len(set(step.requires_success_of)) != len(step.requires_success_of):
                raise ValueError(f"Workflow step has duplicate success requirements: {step.step_id}")
            for required_step in step.requires_success_of:
                if required_step not in step.depends_on:
                    raise ValueError(
                        f"Step {step.step_id} must depend on {required_step!r} before requiring its success"
                    )
            for reference in step.input_mapping.values():
                source = reference.split(".", 1)[0]
                if source == "$input":
                    continue
                if source not in step_ids:
                    raise ValueError(f"Unknown input mapping source {source!r} in step {step.step_id}")
                if source not in step.depends_on:
                    raise ValueError(
                        f"Step {step.step_id} must declare {source!r} in depends_on for its input mapping"
                    )
        self._validate_acyclic(item)
        self.workflow_definitions[item.workflow_id] = item

    def add_workflow_instance(self, item: WorkflowInstance) -> None:
        if item.instance_id in self.workflow_instances:
            raise ValueError(f"Duplicate workflow instance: {item.instance_id}")
        workflow = self.workflow_definitions.get(item.workflow_definition_id)
        if workflow is None:
            raise ValueError(f"Unknown workflow definition: {item.workflow_definition_id}")
        steps_by_id = {step.step_id: step for step in workflow.steps}
        if set(item.step_bindings) != set(steps_by_id):
            raise ValueError("Workflow instance must bind every workflow step exactly once")
        for step_id, module_instance_id in item.step_bindings.items():
            module_instance = self.instances.get(module_instance_id)
            if module_instance is None:
                raise ValueError(f"Unknown module instance for step {step_id}: {module_instance_id}")
            if module_instance.module != steps_by_id[step_id].module_id:
                raise ValueError(f"Module instance capability does not match workflow step {step_id}")
        self.workflow_instances[item.instance_id] = item

    @staticmethod
    def _validate_acyclic(workflow: WorkflowDefinition) -> None:
        dependencies = {step.step_id: set(step.depends_on) for step in workflow.steps}
        resolved: set[str] = set()
        while True:
            ready = {key for key, deps in dependencies.items() if deps <= resolved}
            if not ready:
                break
            resolved.update(ready)
            for key in ready:
                dependencies.pop(key)
        if dependencies:
            raise ValueError(f"Workflow graph has a cycle involving: {', '.join(sorted(dependencies))}")
