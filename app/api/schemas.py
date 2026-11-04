"""API request, response, and SSE streaming event schemas."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.rag.memory.base import ConversationSession, ConversationTurn, SessionInfo
from app.rag.workflows.base import WorkflowInfo, WorkflowInput, WorkflowOutput


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
    rewind_turn_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional turn index from which to rewind and re-execute atomically",
    )
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
    variant_id: str | None = Field(default=None, description="Optional execution variant identifier")
    parameters: dict[str, Any] | None = Field(
        default=None,
        description="Dynamic execution parameters and overrides",
    )

    @field_validator("session_id", mode="before")
    @classmethod
    def sanitize_session_id(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            v_str = v.strip()
            if not v_str or v_str.lower() in ("undefined", "null", '""', "''"):
                return None
            return v_str
        return str(v)

    def to_workflow_input(self) -> WorkflowInput:
        """Translate transport ChatRequest to domain WorkflowInput."""
        messages = None
        if self.messages:
            messages = [m.model_dump() for m in self.messages]
        attachments = None
        if self.attachments:
            attachments = [a.model_dump() for a in self.attachments]
        return WorkflowInput(
            question=self.question,
            workflow=self.workflow,
            session_id=self.session_id,
            rewind_turn_index=self.rewind_turn_index,
            user_id=self.user_id,
            trace_id=self.trace_id,
            stream_steps=self.stream_steps,
            messages=messages,
            attachments=attachments,
            variant_id=self.variant_id,
            parameters=self.parameters or {},
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
    variant_id: str | None = Field(default=None, description="Execution variant identifier")
    session_id: str | None = Field(default=None, description="Conversational session identifier")

    @classmethod
    def from_workflow_output(cls, output: WorkflowOutput) -> ChatResponse:
        """Construct transport ChatResponse from domain WorkflowOutput."""
        return cls(
            workflow=output.workflow,
            answer=output.answer,
            source_used=output.source_used,
            citations=output.citations,
            trace=output.trace,
            rewritten_query=output.rewritten_query,
            trace_id=output.trace_id,
            variant_id=output.variant_id,
            session_id=output.session_id,
        )


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
    citations: list[dict[str, Any]] = Field(
        default_factory=list, description="Document citations and source references"
    )
    rewritten_query: str | None = Field(default=None, description="Rewritten search query if applicable")
    variant_id: str | None = Field(default=None, description="Execution variant identifier")


class StreamErrorEvent(BaseModel):
    """SSE event emitted if an error occurs mid-stream after headers are committed."""

    message: str = Field(..., description="Error message description")
    code: int = Field(default=500, description="HTTP status code corresponding to the error")


class ChatTurnResponse(BaseModel):
    """Rich conversational turn response model."""

    turn_index: int = Field(..., ge=0, description="Sequential index of the conversational turn")
    user_query: str = Field(..., description="User query or question text")
    assistant_answer: str = Field(..., description="Final assistant answer text")
    attachments: list[dict[str, Any]] = Field(default_factory=list, description="Turn attachments")
    citations: list[dict[str, Any]] = Field(default_factory=list, description="Source citations")
    source_used: str = Field(default="direct", description="Information source utilized")
    trace_id: str | None = Field(default=None, description="Optional Langfuse trace identifier")
    created_at: datetime = Field(..., description="Turn creation timestamp")

    @classmethod
    def from_domain(cls, turn: ConversationTurn) -> ChatTurnResponse:
        """Construct ChatTurnResponse from domain ConversationTurn."""
        return cls(
            turn_index=turn.turn_index,
            user_query=turn.user_query,
            assistant_answer=turn.assistant_answer,
            attachments=turn.attachments,
            citations=turn.citations,
            source_used=turn.source_used,
            trace_id=turn.trace_id,
            created_at=turn.created_at,
        )


class SessionSummaryResponse(BaseModel):
    """Metadata summary of a conversation session."""

    session_id: str = Field(..., description="Unique conversational session identifier")
    title: str = Field(..., description="Descriptive title or first query preview")
    parent_session_id: str | None = Field(default=None, description="Lineage identifier if branched")
    forked_from_turn: int | None = Field(default=None, description="Turn index where branched")
    created_at: datetime = Field(..., description="Session creation timestamp")
    updated_at: datetime = Field(..., description="Session last updated timestamp")
    turn_count: int = Field(default=0, ge=0, description="Total count of turns in this session")

    @classmethod
    def from_session_info(cls, info: SessionInfo) -> SessionSummaryResponse:
        """Construct SessionSummaryResponse from domain SessionInfo."""
        return cls(
            session_id=info.session_id,
            title=info.title,
            parent_session_id=info.parent_session_id,
            forked_from_turn=info.forked_from_turn,
            created_at=info.created_at,
            updated_at=info.updated_at,
            turn_count=info.turn_count,
        )


class SessionDetailResponse(BaseModel):
    """Detailed conversation session representation including turn history."""

    session_id: str = Field(..., description="Unique conversational session identifier")
    title: str = Field(..., description="Descriptive title or first query preview")
    parent_session_id: str | None = Field(default=None, description="Lineage identifier if branched")
    forked_from_turn: int | None = Field(default=None, description="Turn index where branched")
    created_at: datetime = Field(..., description="Session creation timestamp")
    updated_at: datetime = Field(..., description="Session last updated timestamp")
    turn_count: int = Field(default=0, ge=0, description="Total count of turns in this session")
    turns: list[ChatTurnResponse] = Field(default_factory=list, description="Sequential conversational turns")

    @classmethod
    def from_domain(
        cls,
        session: ConversationSession | SessionInfo,
        turns: list[ConversationTurn] | None = None,
    ) -> SessionDetailResponse:
        """Construct SessionDetailResponse from domain SessionInfo/ConversationSession and ConversationTurns."""
        resolved_turns = turns if turns is not None else (session.turns if isinstance(session, ConversationSession) else [])
        return cls(
            session_id=session.session_id,
            title=session.title,
            parent_session_id=session.parent_session_id,
            forked_from_turn=session.forked_from_turn,
            created_at=session.created_at,
            updated_at=session.updated_at,
            turn_count=session.turn_count,
            turns=[ChatTurnResponse.from_domain(t) for t in resolved_turns],
        )


class SessionListResponse(BaseModel):
    """Collection response for enumerated conversation sessions."""

    sessions: list[SessionSummaryResponse] = Field(..., description="List of session summaries")
    total: int = Field(..., ge=0, description="Total number of sessions returned")


class RewindRequest(BaseModel):
    """Payload contract for rewinding/truncating conversation history."""

    turn_index: int = Field(..., ge=0, description="Turn index from which to rewind and truncate")


class ForkRequest(BaseModel):
    """Payload contract for branching a conversation into a new session."""

    upto_turn_index: int = Field(
        ..., ge=-1, description="Upper bound turn index to copy into fork (-1 for empty branch)"
    )
    new_session_id: str | None = Field(default=None, description="Optional target session ID for the branch")


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
    "ChatTurnResponse",
    "SessionSummaryResponse",
    "SessionDetailResponse",
    "SessionListResponse",
    "RewindRequest",
    "ForkRequest",
]
