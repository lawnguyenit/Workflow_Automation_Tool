from __future__ import annotations

import logging
import hmac
import os
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from workflow_automation import __version__
from workflow_automation.contracts import ExecutionContext, ModuleError, ModuleResult, ResultState, Retryability, RunMode
from workflow_automation.registry_factory import build_registry
from workflow_automation.runner import WorkflowRunner
from workflow_automation.infrastructure.postgres_workflow_incidents import (
    PostgresWorkflowIncidentLog,
    WorkflowIncidentLogError,
)

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


def _authorize_incident_access(authorization: str | None) -> None:
    expected = os.getenv("WORKFLOW_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="Incident access requires WORKFLOW_API_KEY")
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid bearer token required")


def _record_incident(incident_log: PostgresWorkflowIncidentLog, *, workflow_id: str,
                     context: ExecutionContext, result: dict[str, Any]) -> dict[str, Any] | None:
    if context.mode not in (RunMode.MOCK, RunMode.REAL):
        return None
    try:
        return incident_log.record_result(
            workflow_id=workflow_id, run_id=context.run_id, attempt=context.attempt,
            mode=context.mode.value, result=result,
        )
    except WorkflowIncidentLogError as exc:
        logger.warning("Workflow incident could not be recorded workflow_id=%s error_code=%s",
                       workflow_id, exc.code)
    except Exception as exc:
        logger.warning("Workflow incident could not be recorded workflow_id=%s exception_type=%s",
                       workflow_id, type(exc).__name__)
    return None


def _public_workflow_result(result: dict[str, Any]) -> dict[str, Any]:
    # n8n may retain execution data. Keep provenance and extracted business fields,
    # but never echo original attachments or extracted PDF text in aggregate output.
    public_result = dict(result)
    public_steps = {}
    for step_id, step_result in result["steps"].items():
        projected = dict(step_result)
        if step_id in {"email", "document"}:
            projected["business_result"] = {}
        elif step_id in {"map", "intake", "qualify"}:
            lead = projected.get("business_result", {}).get("lead")
            projected["business_result"] = {
                **({"qualification": lead.get("qualification", {})}
                   if step_id != "map" and isinstance(lead, dict) else {}),
                **({"prompt_version": projected.get("business_result", {}).get("prompt_version")}
                   if step_id == "qualify" else {}),
                **({"mapped": True} if step_id == "map" and isinstance(lead, dict) else {}),
            }
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


class IncidentResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution_code: Literal[
        "RETRY_COMPLETED", "PROVIDER_STATE_CONFIRMED", "MANUAL_REPAIR",
        "DUPLICATE_CONFIRMED", "DISMISSED",
    ]


def create_app() -> FastAPI:
    registry = build_registry()
    runner = WorkflowRunner(registry)
    app = FastAPI(title="Workflow Automation Module API", version=__version__)
    app.state.registry = registry
    app.state.runner = runner
    app.state.incident_log = PostgresWorkflowIncidentLog()

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

    @app.get("/v1/incidents")
    def list_incidents(include_resolved: bool = False, limit: int = Query(default=50, ge=1, le=100),
                       authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize_incident_access(authorization)
        try:
            return {"incidents": app.state.incident_log.list_incidents(
                include_resolved=include_resolved, limit=limit)}
        except WorkflowIncidentLogError as exc:
            raise HTTPException(status_code=503, detail=exc.code) from None

    @app.post("/v1/incidents/{incident_id}/resolve")
    def resolve_incident(incident_id: str, request: IncidentResolutionRequest,
                         authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize_incident_access(authorization)
        try:
            result = app.state.incident_log.resolve(
                incident_id=incident_id, resolution_code=request.resolution_code)
        except WorkflowIncidentLogError as exc:
            status = 400 if exc.code in {"INVALID_INCIDENT_ID", "INVALID_RESOLUTION_CODE"} else 503
            raise HTTPException(status_code=status, detail=exc.code) from None
        if not result["found"]:
            raise HTTPException(status_code=404, detail="Incident not found")
        return result

    @app.get("/v1/incidents/{incident_id}")
    def get_incident(incident_id: str,
                     authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize_incident_access(authorization)
        try:
            result = app.state.incident_log.get_incident(incident_id=incident_id)
        except WorkflowIncidentLogError as exc:
            status = 400 if exc.code == "INVALID_INCIDENT_ID" else 503
            raise HTTPException(status_code=status, detail=exc.code) from None
        if result is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return result

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
        public_result = result.model_dump(mode="json")
        if instance.module in {"lead.intake", "lead.qualify", "source.google_form_response",
                               "source.google_forms_api_response"}:
            lead = public_result.get("business_result", {}).get("lead")
            if instance.module in {"source.google_form_response", "source.google_forms_api_response"}:
                public_result["business_result"] = {"mapped": isinstance(lead, dict)}
            else:
                public_result["business_result"] = {
                    "qualification": lead.get("qualification", {}) if isinstance(lead, dict) else {},
                    "prompt_version": public_result.get("business_result", {}).get("prompt_version"),
                }
        incident = _record_incident(
            app.state.incident_log,
            workflow_id=f"module:{instance_id}",
            context=context,
            result={"status": public_result["status"], "steps": {"execute": public_result}},
        )
        if incident:
            public_result["incident"] = incident
        return public_result

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
        if workflow_id in {"lead_management_intake", "lead_management_google_form",
                           "lead_management_google_forms_api"}:
            payload.setdefault("criteria", None)
            payload.setdefault("mock_qualification", None)
            payload.setdefault("replay_qualification", None)
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
            _record_incident(
                app.state.incident_log,
                workflow_id=workflow_id,
                context=context,
                result={"status": "FAILURE", "steps": {"workflow": {
                    "module_id": "workflow.runner", "status": "FAILURE",
                    "errors": [{"code": "WORKFLOW_INVOCATION_FAILED", "category": "RUNTIME",
                                "retryability": "UNKNOWN"}],
                }}},
            )
            raise HTTPException(status_code=500, detail="Workflow invocation failed; inspect server diagnostics") from None
        logger.info("Workflow run finished workflow_id=%s run_id=%s status=%s", workflow_id,
                    context.run_id, result["status"])
        public_result = _public_workflow_result(result)
        incident = _record_incident(app.state.incident_log, workflow_id=workflow_id,
                                    context=context, result=result)
        if incident:
            public_result["incident"] = incident
        return public_result

    @app.post("/v1/workflow-instances/{workflow_instance_id}/runs")
    def run_workflow_instance(workflow_instance_id: str, request: WorkflowRunRequest,
                              authorization: str | None = Header(default=None)) -> dict[str, Any]:
        instance = registry.workflow_instances.get(workflow_instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="Unknown workflow instance")
        workflow_id = instance.workflow_definition_id
        payload = request.payload
        if workflow_id in {"lead_management_intake", "lead_management_google_form",
                           "lead_management_google_forms_api"}:
            payload.setdefault("criteria", None)
            payload.setdefault("mock_qualification", None)
            payload.setdefault("replay_qualification", None)
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
            _record_incident(
                app.state.incident_log,
                workflow_id=workflow_id,
                context=context,
                result={"status": "FAILURE", "steps": {"workflow": {
                    "module_id": "workflow.runner", "status": "FAILURE",
                    "errors": [{"code": "WORKFLOW_INVOCATION_FAILED", "category": "RUNTIME",
                                "retryability": "UNKNOWN"}],
                }}},
            )
            raise HTTPException(status_code=500, detail="Workflow invocation failed; inspect server diagnostics") from None
        logger.info("Workflow instance run finished instance_id=%s run_id=%s status=%s",
                    workflow_instance_id, context.run_id, result["status"])
        public_result = _public_workflow_result(result)
        incident = _record_incident(app.state.incident_log, workflow_id=workflow_id,
                                    context=context, result=result)
        if incident:
            public_result["incident"] = incident
        return public_result

    return app


app = create_app()
