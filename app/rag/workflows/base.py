"""Base protocol and metadata models for multi-workflow platform."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator


class WorkflowInfo(BaseModel):
    """Metadata declaring workflow capabilities, UI step labels, and generation nodes."""

    id: str = Field(..., description="Unique slug identifying the workflow")
    name: str = Field(..., description="Human-readable display name")
    description: str = Field(..., description="Short explanation of workflow purpose and behavior")
    is_conversational: bool = Field(default=False, description="Whether the workflow maintains conversational history")
    supports_streaming: bool = Field(default=True, description="Whether the workflow supports real-time token streaming")
    step_labels: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from internal graph node identifiers to UI step labels",
    )
    generation_nodes: set[str] = Field(
        default_factory=set,
        description="Set of graph node identifiers whose LLM outputs should stream tokens to the client",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata tags for workflow capabilities and telemetry",
    )


class EvalContext(BaseModel):
    """Normalized evidence extraction for online evaluation tasks."""

    source_used: str = Field(default="direct", description="Primary evidence source used to answer")
    retrieved_contexts: list[str] = Field(
        default_factory=list,
        description="List of text chunks or document excerpts retrieved by the workflow",
    )


class WorkflowInput(BaseModel):
    """Normalized domain input contract for all workflow executions."""

    question: str = Field(..., description="User query or inquiry text")
    messages: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional multi-turn conversation messages preceding this query",
    )
    attachments: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional file, image, or media attachments",
    )
    session_id: str | None = Field(default=None, description="Optional conversational session identifier")
    user_id: str | None = Field(default=None, description="Optional authenticated user identifier")
    trace_id: str | None = Field(default=None, description="Optional telemetry trace identifier")
    variant_id: str | None = Field(default=None, description="Optional execution variant identifier")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Dynamic execution parameters and overrides",
    )


class WorkflowOutput(BaseModel):
    """Normalized domain output contract produced by workflow executions."""

    workflow: str = Field(..., description="Unique slug of the workflow executed")
    answer: str = Field(..., description="Final assistant response text")
    source_used: str = Field(default="direct", description="Primary information source utilized")
    citations: list[Any] = Field(
        default_factory=list,
        description="Cited reference materials and document excerpts",
    )
    trace: list[str] = Field(
        default_factory=list,
        description="Reasoning and retrieval transition trace",
    )
    rewritten_query: str | None = Field(default=None, description="Rewritten or original query")
    trace_id: str | None = Field(default=None, description="Telemetry trace identifier")
    variant_id: str | None = Field(default=None, description="Execution variant identifier")


@runtime_checkable
class WorkflowDefinition(Protocol):
    """Structural protocol defining lifecycle contracts for all workflow topologies."""

    info: WorkflowInfo

    def get_compiled_graph(self) -> Any:
        """Return the compiled, thread-safe LangGraph runnable."""
        ...

    def to_initial_state(self, input: WorkflowInput, trace_id: str | None = None) -> dict[str, Any]:
        """Normalize inbound WorkflowInput into workflow-private graph state."""
        ...

    def to_workflow_output(self, final_state: dict[str, Any], trace_id: str | None = None) -> WorkflowOutput:
        """Convert final internal graph state into standard domain WorkflowOutput."""
        ...

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        """Extract evidence contexts and source used for online evaluation."""
        ...

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        """Synthesize graceful fallback state in the event of an unhandled node failure."""
        ...


__all__ = [
    "WorkflowInfo",
    "EvalContext",
    "WorkflowInput",
    "WorkflowOutput",
    "WorkflowDefinition",
]
