from __future__ import annotations

import os
from typing import Any, Protocol

from workflow_automation.contracts import ExecutionContext, ModuleResult


class Executor(Protocol):
    runtime: str

    def invoke(self, module: Any, payload: dict[str, Any], context: ExecutionContext,
               *, instance_id: str | None = None) -> ModuleResult:
        """Invoke an implementation and return the shared result contract."""


class PythonExecutor:
    runtime = "python"

    def invoke(self, module: Any, payload: dict[str, Any], context: ExecutionContext,
               *, instance_id: str | None = None) -> ModuleResult:
        return module.execute(payload, context)


class HttpApiExecutor:
    """Invoke a module API over HTTP while carrying the shared execution context."""

    runtime = "http"

    def __init__(self, base_url: str, timeout_seconds: float = 30.0,
                 api_key: str | None = None, client: Any | None = None) -> None:
        import httpx

        self._api_key = api_key if api_key is not None else os.getenv("WORKFLOW_API_KEY")
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    def invoke(self, module: Any, payload: dict[str, Any], context: ExecutionContext,
               *, instance_id: str | None = None) -> ModuleResult:
        if not instance_id:
            raise ValueError("HTTP executor requires the concrete module instance ID")
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        response = self._client.post(
            f"/v1/instances/{instance_id}/runs",
            json={"payload": payload, "context": context.model_dump(mode="json")},
            headers=headers,
        )
        response.raise_for_status()
        return ModuleResult.model_validate(response.json())

    def close(self) -> None:
        self._client.close()
