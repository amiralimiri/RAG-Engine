"""Domain event models representing discrete workflow execution milestones."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.rag.workflows.base import WorkflowOutput


class StepTransitionEvent(BaseModel):
    """Emitted when the execution graph transitions between discrete nodes."""

    type: Literal["step"] = "step"
    step: str = Field(description="Name of the node entered (e.g. 'retrieve', 'grade_evidence')")
    label: str = Field(default="", description="Human-readable progression label")
    timestamp: float = Field(
        default_factory=lambda: datetime.now(UTC).timestamp(),
        description="Chronological UTC milestone timestamp in seconds",
    )

    @model_validator(mode="after")
    def _ensure_label(self) -> Self:
        if not self.label or not self.label.strip():
            self.label = self.step
        return self


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
    StepTransitionEvent | TokenDeltaEvent | ExecutionCompletedEvent | ExecutionFailedEvent,
    Field(discriminator="type"),
]

__all__ = [
    "StepTransitionEvent",
    "TokenDeltaEvent",
    "ExecutionCompletedEvent",
    "ExecutionFailedEvent",
    "WorkflowExecutionEvent",
]
