"""Base protocol and metadata models for multi-workflow platform."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol, TypedDict, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.rag.state import CitationList


class WorkflowInfo(BaseModel):
    """Metadata declaring workflow capabilities, UI step labels, and generation nodes."""

    id: str = Field(..., description="Unique slug identifying the workflow")
    name: str = Field(..., description="Human-readable display name")
    description: str = Field(..., description="Short explanation of workflow purpose and behavior")
    entrypoint: str | None = Field(default=None, description="Dynamic entrypoint specifier module:function")
    is_conversational: bool = Field(default=False, description="Whether the workflow maintains conversational history")
    supports_streaming: bool = Field(
        default=True, description="Whether the workflow supports real-time token streaming"
    )
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

    model_config = ConfigDict(arbitrary_types_allowed=True)

    question: str = Field(..., description="User query or inquiry text")
    workflow: str | None = Field(default=None, description="Optional target LangGraph workflow name")
    messages: Sequence[BaseMessage | dict[str, Any]] | None = Field(
        default=None,
        description="Optional multi-turn conversation messages preceding this query",
    )
    attachments: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional file, image, or media attachments",
    )
    session_id: str | None = Field(default=None, description="Optional conversational session identifier")
    rewind_turn_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional turn index from which to rewind and re-execute atomically",
    )
    user_id: str | None = Field(default=None, description="Optional authenticated user identifier")
    trace_id: str | None = Field(default=None, description="Optional telemetry trace identifier")
    variant_id: str | None = Field(default=None, description="Optional execution variant identifier")
    stream_steps: bool = Field(default=True, description="Whether to emit intermediate graph step events")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Dynamic execution parameters and overrides",
    )


class ExecutionProfile(BaseModel):
    """Configuration profile declaring execution variant parameters, model settings, and telemetry tags."""

    variant_id: str = Field(..., description="Unique identifier for the execution variant")
    model_name: str | None = Field(default=None, description="Optional LLM model name or identifier")
    prompt_version: str | None = Field(default=None, description="Optional prompt version or template identifier")
    temperature: float | None = Field(default=None, description="Optional model sampling temperature")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary parameters and overrides for graph configuration",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags to attach to telemetry traces",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured metadata to attach to telemetry traces",
    )


class WorkflowOutput(BaseModel):
    """Normalized domain output contract produced by workflow executions."""

    workflow: str = Field(..., description="Unique slug of the workflow executed")
    answer: str = Field(..., description="Final assistant response text")
    source_used: str = Field(default="direct", description="Primary information source utilized")
    citations: CitationList = Field(
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
    session_id: str | None = Field(default=None, description="Conversational session identifier")
    retrieved_contexts: list[str] = Field(
        default_factory=list,
        description="List of text chunks or document excerpts retrieved by the workflow",
    )

    def normalize_contexts(self) -> list[str]:
        """Normalize raw or heterogeneous retrieved_contexts into a standardized list of strings."""
        raw_contexts = getattr(self, "retrieved_contexts", None)
        if raw_contexts is None:
            self.retrieved_contexts = []
            return self.retrieved_contexts

        if isinstance(raw_contexts, list):
            normalized: list[str] = []
            for chunk_item in raw_contexts:
                if isinstance(chunk_item, str):
                    normalized.append(chunk_item)
                elif isinstance(chunk_item, dict):
                    content = None
                    for key in ("content", "text", "snippet", "page_content"):
                        val = chunk_item.get(key)
                        if val is not None:
                            content = val
                            break
                    normalized.append(str(content) if content is not None else str(chunk_item))
                else:
                    normalized.append(str(chunk_item))
            self.retrieved_contexts = normalized
        elif isinstance(raw_contexts, str):
            self.retrieved_contexts = [raw_contexts]
        else:
            self.retrieved_contexts = [str(raw_contexts)]
        return self.retrieved_contexts


@runtime_checkable
class WorkflowDefinition(Protocol):
    """Structural protocol defining lifecycle contracts for all workflow topologies."""

    info: WorkflowInfo

    def get_compiled_graph(self, variant_id: str | None = None) -> Any:
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


class BoundExecutionContext(BaseModel):
    """Immutable domain context bundling resolved workflow, state, config, and telemetry."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    workflow: WorkflowDefinition
    initial_state: dict[str, Any]
    config: dict[str, Any]
    trace_id: str
    session_id: str | None = None
    variant_id: str | None = None
    resolved_parameters: dict[str, Any] = Field(default_factory=dict)
    hydrated_messages: list[BaseMessage] | None = None

    def as_runnable_config(self) -> dict[str, Any]:
        """Return defensive shallow copy of config suitable for LangGraph runnables."""
        return dict(self.config)


class StandardWorkflowState(TypedDict, total=False):
    """Canonical state dictionary contract for pluggable workflow topologies."""

    question: str
    messages: Sequence[BaseMessage]
    attachments: list[dict[str, Any]]
    session_id: str | None
    trace_id: str | None
    answer: str
    retrieved_contexts: list[str]
    source_used: str
    citations: list[dict[str, Any]]
    trace: list[str]
    rewritten_query: str | None


def _extract_text_content(content: Any) -> str:
    """Extract plain string content from string, list of blocks, or object."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", "")))
            elif hasattr(c, "text"):
                parts.append(str(getattr(c, "text", "") or ""))
            else:
                parts.append(str(c))
        return "".join(parts)
    return str(content) if content is not None else ""


def _extract_answer(state: dict[str, Any]) -> str:
    """Extract answer text from state['answer'] or trailing AIMessage in state['messages']."""
    raw_answer = state.get("answer")
    if raw_answer:
        if isinstance(raw_answer, AIMessage):
            ans = _extract_text_content(raw_answer.content)
            if ans.strip():
                return ans
        elif isinstance(raw_answer, str):
            if raw_answer.strip():
                return raw_answer
        else:
            ans = _extract_text_content(raw_answer)
            if ans.strip():
                return ans

    messages = state.get("messages")
    if isinstance(messages, Sequence):
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                text = _extract_text_content(m.content)
                if text.strip():
                    return text
            elif isinstance(m, dict) and (m.get("role") or "").lower() in ("assistant", "ai"):
                text = _extract_text_content(m.get("content", ""))
                if text.strip():
                    return text
            elif hasattr(m, "role") and str(getattr(m, "role", "")).lower() in ("assistant", "ai"):
                text = _extract_text_content(getattr(m, "content", ""))
                if text.strip():
                    return text

    return ""


def parse_raw_messages(
    raw_messages: Sequence[Any] | None,
    question: str | None = None,
) -> list[BaseMessage]:
    """Parse raw message dicts, objects, or BaseMessage instances into LangChain BaseMessage instances."""
    messages: list[BaseMessage] = []
    if raw_messages:
        for m in raw_messages:
            if isinstance(m, BaseMessage):
                messages.append(m)
            elif isinstance(m, dict):
                role = (m.get("role") or "").lower()
                content = m.get("content", "")
                if role in ("user", "human"):
                    messages.append(HumanMessage(content=content))
                elif role in ("assistant", "ai"):
                    messages.append(AIMessage(content=content))
                elif role == "system":
                    messages.append(SystemMessage(content=content))
                else:
                    messages.append(HumanMessage(content=content))
            else:
                role = (getattr(m, "role", "") or "").lower()
                content = getattr(m, "content", "")
                if role in ("user", "human"):
                    messages.append(HumanMessage(content=content))
                elif role in ("assistant", "ai"):
                    messages.append(AIMessage(content=content))
                elif role == "system":
                    messages.append(SystemMessage(content=content))
                else:
                    messages.append(HumanMessage(content=content))

    if question:
        if not messages or getattr(messages[-1], "content", None) != question:
            messages.append(HumanMessage(content=question))
    return messages


def parse_raw_attachments(raw_attachments: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Normalize raw attachment dictionaries or models into list of dictionaries."""
    attachments: list[dict[str, Any]] = []
    if raw_attachments:
        for a in raw_attachments:
            if isinstance(a, dict):
                attachments.append(a)
            elif hasattr(a, "model_dump"):
                attachments.append(a.model_dump())
            else:
                attachments.append(dict(a))
    return attachments


class PluggableWorkflow:
    """Generic adapter wrapping compiled LangGraph runnables or factory callables."""

    def __init__(
        self,
        info: WorkflowInfo,
        graph: Any | Callable[..., Any],
    ) -> None:
        self.info = info
        self._raw_graph = graph
        self._lock = threading.RLock()
        self._compiled_graph: Any | None = None
        self._variant_graphs: dict[str, Any] = {}

        if hasattr(graph, "invoke") or hasattr(graph, "ainvoke"):
            self._compiled_graph = graph
            self._graph_factory = None
        elif callable(graph):
            self._compiled_graph = None
            self._graph_factory = graph
        else:
            self._compiled_graph = graph
            self._graph_factory = None

    def get_compiled_graph(self, variant_id: str | None = None) -> Any:
        """Return the compiled, thread-safe LangGraph runnable."""
        with self._lock:
            if variant_id is not None:
                if variant_id in self._variant_graphs:
                    return self._variant_graphs[variant_id]
                if self._graph_factory is not None:
                    config = {"variant_id": variant_id}
                    try:
                        vg = self._graph_factory(config=config)  # type: ignore
                    except TypeError:
                        try:
                            vg = self._graph_factory(config)  # type: ignore
                        except TypeError:
                            vg = self._graph_factory()
                    self._variant_graphs[variant_id] = vg
                    return vg
                if self._compiled_graph is not None:
                    return self._compiled_graph

            if self._compiled_graph is None:
                if self._graph_factory is not None:
                    try:
                        self._compiled_graph = self._graph_factory(config=None)  # type: ignore
                    except TypeError:
                        try:
                            self._compiled_graph = self._graph_factory(None)  # type: ignore
                        except TypeError:
                            self._compiled_graph = self._graph_factory()
                else:
                    self._compiled_graph = self._raw_graph
            return self._compiled_graph

    def to_initial_state(
        self,
        input: WorkflowInput | dict[str, Any] | Any,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Normalize inbound WorkflowInput into canonical StandardWorkflowState."""
        if isinstance(input, dict):
            raw_question = input.get("question", "")
            raw_messages = input.get("messages")
            raw_attachments = input.get("attachments")
            session_id = input.get("session_id")
            resolved_tid = trace_id or input.get("trace_id")
            variant_id = input.get("variant_id")
        else:
            raw_question = getattr(input, "question", "")
            raw_messages = getattr(input, "messages", None)
            raw_attachments = getattr(input, "attachments", None)
            session_id = getattr(input, "session_id", None)
            resolved_tid = trace_id or getattr(input, "trace_id", None)
            variant_id = getattr(input, "variant_id", None)

        messages = parse_raw_messages(raw_messages, raw_question)
        question = raw_question or ""
        if not question and messages:
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    question = str(m.content)
                    break

        attachments = parse_raw_attachments(raw_attachments)

        state: dict[str, Any] = {
            "question": question,
            "messages": messages,
            "attachments": attachments,
            "session_id": session_id,
            "trace_id": resolved_tid,
            "answer": "",
            "retrieved_contexts": [],
            "source_used": "",
            "citations": [],
            "trace": [],
            "rewritten_query": None,
        }
        if variant_id is not None:
            state["variant_id"] = variant_id

        return state

    def to_workflow_output(
        self,
        final_state: dict[str, Any],
        trace_id: str | None = None,
    ) -> WorkflowOutput:
        """Convert internal graph state into standard domain WorkflowOutput."""
        answer = _extract_answer(final_state)
        resolved_tid = trace_id or final_state.get("trace_id")

        source_used = final_state.get("source_used")
        if not source_used:
            if final_state.get("retrieved_contexts"):
                source_used = "knowledge_base"
            else:
                source_used = "direct"

        citations: list[dict[str, Any]] = []
        for c in final_state.get("citations") or []:
            if isinstance(c, dict):
                citations.append(c)
            elif hasattr(c, "model_dump"):
                citations.append(c.model_dump())
            else:
                citations.append({"text": str(c)})

        if not citations:
            messages = final_state.get("messages")
            if isinstance(messages, Sequence):
                for m in messages:
                    artifact = getattr(m, "artifact", None)
                    if isinstance(artifact, list):
                        for item in artifact:
                            if isinstance(item, dict) and item not in citations:
                                citations.append(item)

        raw_trace = final_state.get("trace")
        if isinstance(raw_trace, list):
            trace = [str(t) for t in raw_trace]
        elif raw_trace:
            trace = [str(raw_trace)]
        else:
            trace = []

        rewritten_query = final_state.get("rewritten_query")
        if not rewritten_query:
            rewritten_query = final_state.get("question")
        if not rewritten_query:
            messages = final_state.get("messages")
            if isinstance(messages, Sequence):
                for m in reversed(messages):
                    if isinstance(m, HumanMessage):
                        rewritten_query = _extract_text_content(m.content)
                        break
                    if isinstance(m, dict) and (m.get("role") or "").lower() in ("user", "human"):
                        rewritten_query = _extract_text_content(m.get("content", ""))
                        break

        return WorkflowOutput(
            workflow=self.info.id,
            answer=answer,
            source_used=str(source_used),
            citations=citations,
            trace=trace,
            rewritten_query=rewritten_query,
            trace_id=resolved_tid,
            variant_id=final_state.get("variant_id"),
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        """Extract evidence contexts and source used for online evaluation."""
        source_used = final_state.get("source_used") or "direct"
        raw_contexts = final_state.get("retrieved_contexts") or []
        retrieved_contexts: list[str] = []
        if isinstance(raw_contexts, list):
            for c in raw_contexts:
                if isinstance(c, str):
                    retrieved_contexts.append(c)
                elif isinstance(c, dict):
                    retrieved_contexts.append(str(c.get("content") or c.get("text") or c))
                else:
                    retrieved_contexts.append(str(c))
        elif isinstance(raw_contexts, str):
            retrieved_contexts.append(raw_contexts)

        if not retrieved_contexts:
            messages = final_state.get("messages")
            if isinstance(messages, Sequence):
                for m in messages:
                    if hasattr(m, "content") and (
                        getattr(m, "type", "") == "tool" or m.__class__.__name__ == "ToolMessage"
                    ):
                        retrieved_contexts.append(str(m.content))
                    elif isinstance(m, dict) and m.get("role") == "tool":
                        retrieved_contexts.append(str(m.get("content", "")))

        return EvalContext(
            source_used=str(source_used),
            retrieved_contexts=retrieved_contexts,
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        """Synthesize graceful fallback state in the event of an unhandled node failure."""
        err_msg = (
            "I encountered an unexpected issue while processing your request. "
            "Please try again or contact support if the problem persists."
        )
        existing_trace = current_state.get("trace", []) if isinstance(current_state.get("trace"), list) else []
        fallback_trace = list(existing_trace)
        fallback_trace.append(f"Workflow error fallback: {str(exc)}")

        return {
            "question": current_state.get("question", ""),
            "messages": current_state.get("messages", []),
            "attachments": current_state.get("attachments", []),
            "session_id": current_state.get("session_id"),
            "trace_id": current_state.get("trace_id"),
            "answer": err_msg,
            "source_used": "error_fallback",
            "retrieved_contexts": current_state.get("retrieved_contexts", []),
            "citations": current_state.get("citations", []),
            "trace": fallback_trace,
            "rewritten_query": current_state.get("rewritten_query") or current_state.get("question"),
        }


__all__ = [
    "WorkflowInfo",
    "EvalContext",
    "WorkflowInput",
    "WorkflowOutput",
    "ExecutionProfile",
    "WorkflowDefinition",
    "BoundExecutionContext",
    "StandardWorkflowState",
    "PluggableWorkflow",
    "parse_raw_messages",
    "parse_raw_attachments",
]
