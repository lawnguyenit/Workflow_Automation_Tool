from __future__ import annotations

from typing import Any, Protocol

from workflow_automation.contracts import ExecutionContext, ModuleResult


class Module(Protocol):
    module_id: str

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        """Execute one capability and return a normalized operational result."""
