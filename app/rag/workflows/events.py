"""Domain event models representing discrete workflow execution milestones."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.rag.workflows.base import WorkflowOutput


class StepTransitionEvent(BaseModel):
    """Emitted when the execution graph transitions between discrete nodes."""

    type: Literal["step"] = "step"
    step: str = Field(description="Name of the node entered (e.g. 'retrieve', 'grade_evidence')")


class TokenDeltaEvent(BaseModel):
    """Emitted when a generation node produces an LLM token chunk."""

    type: Literal["token"] = "token"
    delta: str = Field(description="Generated textual token content")


class ExecutionCompletedEvent(BaseModel):
    """Emitted upon successful completion of workflow execution."""

    type: Literal["completed"] = "completed"
    output: WorkflowOutput = Field(description="Canonical workflow domain output")


class ExecutionFailedEvent(BaseModel):
    """Emitted when workflow execution fails with an uncaught exception."""

    type: Literal["failed"] = "failed"
    error: str = Field(description="Human-readable error description")
    code: str = Field(default="EXECUTION_ERROR", description="Machine-readable error code")


WorkflowExecutionEvent = Annotated[
    StepTransitionEvent
    | TokenDeltaEvent
    | ExecutionCompletedEvent
    | ExecutionFailedEvent,
    Field(discriminator="type"),
]

__all__ = [
    "StepTransitionEvent",
    "TokenDeltaEvent",
    "ExecutionCompletedEvent",
    "ExecutionFailedEvent",
    "WorkflowExecutionEvent",
]
