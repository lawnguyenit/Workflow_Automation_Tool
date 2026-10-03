from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: str
    version: str = "1"
    purpose: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    possible_results: list[str]


class ModuleImplementation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation_id: str
    implements: str
    version: str = "0.1.0"
    runtime: str
    provider: str | None = None
    limitations: list[str] = Field(default_factory=list)


class ExecutorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor_id: str
    runtime: str
    version: str = "0.1.0"


class ModuleInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    module: str
    implementation: str
    executor_id: str
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    module_id: str
    input_mapping: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    continue_on_partial: bool = False


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    version: str = "1"
    description: str
    steps: list[WorkflowStep]


class WorkflowInstance(BaseModel):
    """Bind each reusable graph step to a concrete configured module instance."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    workflow_definition_id: str
    step_bindings: dict[str, str]
    description: str = ""
    version: str = "1"
