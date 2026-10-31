"""API request, response, and SSE streaming event schemas."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.rag.workflows.base import WorkflowInfo


class ChatMessage(BaseModel):
    """Conversational chat message."""

    role: str = Field(..., description="Message author role (e.g. 'user', 'assistant', 'system')")
    content: str = Field(..., description="Message text content")


class Attachment(BaseModel):
    """File, image, or media attachment for multimodal requests."""

    filename: str = Field(..., description="Name of the attached file")
    content_type: str = Field(..., description="MIME content type")
    url: str | None = Field(default=None, description="Optional remote URL to resource")


class ChatRequest(BaseModel):
    """Payload contract for chat generation requests."""

    question: str = Field(..., min_length=2, max_length=3000, description="User query or inquiry text")
    workflow: str = Field(default="fix_single_jev", description="Target LangGraph workflow name")
    session_id: str | None = Field(default=None, description="Optional conversational session identifier")
    user_id: str | None = Field(default=None, description="Optional authenticated user identifier")
    trace_id: str | None = Field(default=None, description="Optional Langfuse trace ID override")
    stream_steps: bool = Field(default=True, description="Whether to emit intermediate graph step events")
    messages: list[ChatMessage] | None = Field(
        default=None,
        description="Optional multi-turn conversation messages preceding this query",
    )
    attachments: list[Attachment] | None = Field(
        default=None,
        description="Optional file or image attachments",
    )


class ChatResponse(BaseModel):
    """Standard synchronous chat response contract."""

    workflow: str = Field(..., description="Workflow executed")
    answer: str = Field(..., description="Final assistant response text")
    source_used: str = Field(..., description="Primary information source utilized")
    trace: list[str] = Field(default_factory=list, description="Reasoning and retrieval transition trace")
    citations: list[Any] = Field(default_factory=list, description="Cited reference materials")
    rewritten_query: str | None = Field(default=None, description="Rewritten or original query")
    trace_id: str | None = Field(default=None, description="Langfuse trace ID")


class WorkflowListResponse(BaseModel):
    """Available workflows and rich capability introspection."""

    default: str = Field(..., description="Default workflow name")
    workflows: list[str] = Field(..., description="List of registered workflow names")
    details: list[WorkflowInfo] = Field(
        default_factory=list,
        description="Rich capability details for registered workflows",
    )


class FeedbackRequest(BaseModel):
    """Telemetry scoring payload."""

    trace_id: str = Field(..., min_length=1, description="Langfuse Trace ID")
    score: float = Field(..., ge=0.0, le=1.0, description="Score normalized from 0.0 to 1.0")
    name: str = Field(default="user_feedback", max_length=100, description="Metric name")
    comment: str | None = Field(default=None, max_length=1000, description="Optional feedback comment")


class StreamStepEvent(BaseModel):
    """SSE event emitted when entering or transitioning through a LangGraph node."""

    step: str = Field(..., description="Internal LangGraph step/node identifier")
    label: str = Field(..., description="Human-readable status label")
    timestamp: float = Field(
        default_factory=lambda: datetime.now(UTC).timestamp(),
        description="Event creation timestamp in UTC epoch seconds",
    )


class StreamTokenEvent(BaseModel):
    """SSE event delivering an incremental token delta from active generation."""

    delta: str = Field(..., description="Incremental text token delta from active generation node")


class StreamMetadataEvent(BaseModel):
    """SSE event emitted upon graph completion with final trace and citations."""

    trace_id: str | None = Field(default=None, description="Langfuse trace identifier")
    source_used: str = Field(..., description="Information source used (e.g. private_kb, web, direct)")
    workflow: str = Field(..., description="Workflow executed")
    trace: list[str] = Field(default_factory=list, description="Execution step trace list")
    citations: list[dict[str, Any]] = Field(default_factory=list, description="Document citations and source references")
    rewritten_query: str | None = Field(default=None, description="Rewritten search query if applicable")


class StreamErrorEvent(BaseModel):
    """SSE event emitted if an error occurs mid-stream after headers are committed."""

    message: str = Field(..., description="Error message description")
    code: int = Field(default=500, description="HTTP status code corresponding to the error")


__all__ = [
    "ChatMessage",
    "Attachment",
    "ChatRequest",
    "ChatResponse",
    "WorkflowListResponse",
    "FeedbackRequest",
    "StreamStepEvent",
    "StreamTokenEvent",
    "StreamMetadataEvent",
    "StreamErrorEvent",
]
