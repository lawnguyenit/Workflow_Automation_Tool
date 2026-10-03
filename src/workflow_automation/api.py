from __future__ import annotations

import logging
import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from workflow_automation.contracts import ExecutionContext, ModuleError, ModuleResult, ResultState, Retryability, RunMode
from workflow_automation.registry_factory import build_registry
from workflow_automation.runner import WorkflowRunner

logger = logging.getLogger("workflow_automation.api")


def _authorize_run(context: ExecutionContext, authorization: str | None,
                   *, reads_live_gmail: bool = False) -> None:
    # Preview/replay modes are designed to avoid external providers and database writes.
    # Keep the service network private because invoice data can still pass through them.
    if context.mode in (RunMode.DRY_RUN, RunMode.REPLAY) and not reads_live_gmail:
        return
    expected = os.getenv("WORKFLOW_API_KEY")
    if expected:
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Valid bearer token required")
        return
    raise HTTPException(status_code=503, detail="This execution requires WORKFLOW_API_KEY")


def _public_workflow_result(result: dict[str, Any]) -> dict[str, Any]:
    # n8n may retain execution data. Keep provenance and extracted business fields,
    # but never echo original attachments or extracted PDF text in aggregate output.
    public_result = dict(result)
    public_steps = {}
    for step_id, step_result in result["steps"].items():
        projected = dict(step_result)
        if step_id in {"email", "document"}:
            projected["business_result"] = {}
        public_steps[step_id] = projected
    public_result["steps"] = public_steps
    return public_result


class ModuleRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]
    context: ExecutionContext | None = None


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any] = Field(default_factory=dict)
    context: ExecutionContext | None = None


def create_app() -> FastAPI:
    registry = build_registry()
    runner = WorkflowRunner(registry)
    app = FastAPI(title="Workflow Automation Module API", version="0.1.0")
    app.state.registry = registry
    app.state.runner = runner

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/catalog")
    def catalog() -> dict[str, Any]:
        return {
            "modules": [item.model_dump(mode="json") for item in registry.definitions.values()],
            "implementations": [item.model_dump(mode="json") for item in registry.implementations.values()],
            "instances": [item.model_dump(mode="json") for item in registry.instances.values()],
            "workflows": [item.model_dump(mode="json") for item in registry.workflow_definitions.values()],
            "workflow_instances": [item.model_dump(mode="json") for item in registry.workflow_instances.values()],
        }

    @app.post("/v1/instances/{instance_id}/runs")
    def run_instance(instance_id: str, request: ModuleRunRequest,
                     authorization: str | None = Header(default=None)) -> dict[str, Any]:
        instance = registry.instances.get(instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="Unknown module instance")
        context = request.context or ExecutionContext(workflow_id=f"module:{instance_id}", mode=RunMode.DRY_RUN)
        _authorize_run(context, authorization,
                       reads_live_gmail=instance.module == "email.ingest" and request.payload.get("source") == "gmail")
        try:
            module = registry.instance_modules[instance_id]
            executor = registry.executor_bindings[instance.executor_id]
            result = executor.invoke(module, request.payload, context, instance_id=instance_id)
        except Exception as exc:
            logger.error("Module invocation failed instance_id=%s run_id=%s exception_type=%s",
                         instance_id, context.run_id, type(exc).__name__)
            result = ModuleResult(
                module_id=instance.module,
                status=ResultState.FAILURE,
                errors=[ModuleError(code="MODULE_INVOCATION_FAILED", category="RUNTIME",
                                    message="Module invocation failed; inspect server diagnostics",
                                    retryability=Retryability.UNKNOWN)],
                context=context,
            ).complete()
        logger.info("Module run finished instance_id=%s run_id=%s status=%s", instance_id,
                    context.run_id, result.status.value)
        return result.model_dump(mode="json")

    @app.post("/v1/workflows/{workflow_id}/runs")
    def run_workflow(workflow_id: str, request: WorkflowRunRequest,
                     authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if workflow_id not in registry.workflow_definitions:
            raise HTTPException(status_code=404, detail="Unknown workflow")
        matching_instances = [item for item in registry.workflow_instances.values()
                              if item.workflow_definition_id == workflow_id]
        if len(matching_instances) != 1:
            raise HTTPException(status_code=409, detail="Workflow must have exactly one default instance for this route")
        payload = request.payload
        if workflow_id == "intelligent_document_intake":
            payload = {
                "source": "fixture",
                "fixture_id": "synthetic_invoice",
                "query": "has:attachment",
                "max_results": 10,
                "source_account": "",
                **payload,
            }
        context = request.context or ExecutionContext(workflow_id=workflow_id, mode=RunMode.DRY_RUN)
        _authorize_run(context, authorization, reads_live_gmail=payload.get("source") == "gmail")
        try:
            result = runner.run(matching_instances[0].instance_id, payload, context)
        except Exception as exc:
            logger.error("Workflow invocation failed workflow_id=%s run_id=%s exception_type=%s",
                         workflow_id, context.run_id, type(exc).__name__)
            raise HTTPException(status_code=500, detail="Workflow invocation failed; inspect server diagnostics") from None
        logger.info("Workflow run finished workflow_id=%s run_id=%s status=%s", workflow_id,
                    context.run_id, result["status"])
        return _public_workflow_result(result)

    @app.post("/v1/workflow-instances/{workflow_instance_id}/runs")
    def run_workflow_instance(workflow_instance_id: str, request: WorkflowRunRequest,
                              authorization: str | None = Header(default=None)) -> dict[str, Any]:
        instance = registry.workflow_instances.get(workflow_instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="Unknown workflow instance")
        workflow_id = instance.workflow_definition_id
        payload = request.payload
        if workflow_id == "intelligent_document_intake":
            payload = {
                "source": "fixture", "fixture_id": "synthetic_invoice", "query": "has:attachment",
                "max_results": 10, "source_account": "", **payload,
            }
        context = request.context or ExecutionContext(workflow_id=workflow_id, mode=RunMode.DRY_RUN)
        _authorize_run(context, authorization, reads_live_gmail=payload.get("source") == "gmail")
        try:
            result = runner.run(workflow_instance_id, payload, context)
        except Exception as exc:
            logger.error("Workflow instance invocation failed instance_id=%s run_id=%s exception_type=%s",
                         workflow_instance_id, context.run_id, type(exc).__name__)
            raise HTTPException(status_code=500, detail="Workflow invocation failed; inspect server diagnostics") from None
        logger.info("Workflow instance run finished instance_id=%s run_id=%s status=%s",
                    workflow_instance_id, context.run_id, result["status"])
        return _public_workflow_result(result)

    return app


app = create_app()
