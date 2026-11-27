from __future__ import annotations

from typing import Any

from workflow_automation.contracts import ExecutionContext, ModuleError, ModuleResult, ResultState, Retryability
from workflow_automation.registry import FrameworkRegistry


def _resolve_ref(reference: str, original: dict[str, Any], results: dict[str, ModuleResult]) -> Any:
    parts = reference.split(".")
    if parts[0] == "$input":
        value: Any = original
        parts = parts[1:]
    else:
        if parts[0] not in results:
            raise ValueError(f"Workflow input mapping references unavailable step {parts[0]!r}")
        value = results[parts[0]].business_result
        parts = parts[1:]
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"Workflow input mapping could not resolve {reference!r}")
        value = value[part]
    return value


class WorkflowRunner:
    def __init__(self, registry: FrameworkRegistry) -> None:
        self.registry = registry

    def run(self, workflow_instance_id: str, payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        workflow_instance = self.registry.workflow_instances[workflow_instance_id]
        workflow = self.registry.workflow_definitions[workflow_instance.workflow_definition_id]
        results: dict[str, ModuleResult] = {}
        pending = list(workflow.steps)
        ordered = []
        completed_steps: set[str] = set()
        while pending:
            ready = [step for step in pending if set(step.depends_on) <= completed_steps]
            if not ready:
                raise ValueError("Workflow has unresolved or cyclic dependencies")
            for step in ready:
                ordered.append(step)
                pending.remove(step)
                completed_steps.add(step.step_id)

        for step in ordered:
            failed_requirements = [
                required for required in step.requires_success_of
                if required not in results or results[required].status != ResultState.SUCCESS
            ]
            if failed_requirements:
                continue
            module_instance_id = workflow_instance.step_bindings[step.step_id]
            instance = self.registry.instances[module_instance_id]
            try:
                implementation = self.registry.instance_modules[instance.instance_id]
                executor = self.registry.executor_bindings[instance.executor_id]
                module_input = {
                    key: _resolve_ref(ref, payload, results)
                    for key, ref in step.input_mapping.items()
                }
                result = executor.invoke(implementation, module_input, context, instance_id=instance.instance_id)
            except Exception:  # Normalize at the executor boundary without echoing payload/driver errors.
                code = "INPUT_MAPPING_OR_EXECUTION_FAILED"
                category = "RUNTIME"
                if instance.instance_id not in self.registry.instance_modules:
                    code = "MODULE_IMPLEMENTATION_NOT_BOUND"
                elif instance.executor_id not in self.registry.executor_bindings:
                    code = "EXECUTOR_NOT_BOUND"
                result = ModuleResult(
                    module_id=instance.module,
                    status=ResultState.FAILURE,
                    errors=[ModuleError(
                        code=code,
                        category=category,
                        message=f"Step {step.step_id} could not complete; inspect sanitized execution diagnostics",
                        retryability=Retryability.UNKNOWN,
                    )],
                    context=context,
                ).complete()
            results[step.step_id] = result
            if result.status in (ResultState.FAILURE, ResultState.REVIEW_REQUIRED):
                break
            if result.status == ResultState.PARTIAL and not step.continue_on_partial:
                break

        final = next((results[step.step_id] for step in reversed(ordered)
                      if step.step_id in results), None)
        status_precedence = {
            ResultState.SUCCESS: 0,
            ResultState.PARTIAL: 1,
            ResultState.REVIEW_REQUIRED: 2,
            ResultState.FAILURE: 3,
        }
        overall = max((item.status for item in results.values()),
                      key=lambda status: status_precedence[status],
                      default=ResultState.SUCCESS)
        gated_steps = {
            step.step_id: [required for required in step.requires_success_of
                           if required not in results or results[required].status != ResultState.SUCCESS]
            for step in ordered
            if step.step_id not in results and step.requires_success_of
        }
        return {
            "workflow_id": workflow.workflow_id,
            "workflow_instance_id": workflow_instance.instance_id,
            "workflow_version": workflow.version,
            "status": overall.value,
            "context": context.model_dump(mode="json"),
            "steps": {step_id: result.model_dump(mode="json") for step_id, result in results.items()},
            "skipped_steps": [step.step_id for step in ordered if step.step_id not in results],
            "gated_steps": gated_steps,
            "result": None if final is None else final.business_result,
        }
