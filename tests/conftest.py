"""Root Pytest configuration and shared test isolation fixtures."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, Callable
from unittest.mock import MagicMock
import pytest

from app.api.schemas import ChatRequest, ChatResponse
from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry
from app.rag.workflows.runner import DEFAULT_GENERATION_NODES, DEFAULT_STEP_LABELS
from app.rag.workflows import register_default_workflows


class MockWorkflowDefinition:
    """Lightweight configurable WorkflowDefinition test double for route and integration testing."""

    def __init__(
        self,
        id: str,
        name: str = "Mock Workflow",
        *,
        answer: str = "Mock response",
        source_used: str = "knowledge_base",
        trace: list[str] | None = None,
        citations: list[dict[str, Any]] | None = None,
        rewritten_query: str | None = None,
        retrieved_contexts: list[str] | None = None,
        stream_events: list[dict[str, Any]] | Callable[..., Any] | None = None,
        graph: Any = None,
        description: str | None = None,
        is_conversational: bool = False,
        supports_streaming: bool = True,
        step_labels: dict[str, str] | None = None,
        generation_nodes: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.info = WorkflowInfo(
            id=id,
            name=name,
            description=description or f"Mock workflow definition for {id}",
            is_conversational=is_conversational,
            supports_streaming=supports_streaming,
            step_labels=step_labels if step_labels is not None else dict(DEFAULT_STEP_LABELS),
            generation_nodes=generation_nodes if generation_nodes is not None else (set(DEFAULT_GENERATION_NODES) | {"agent"}),
            metadata=metadata or {},
        )
        self.answer = answer
        self.source_used = source_used
        self.trace = trace if trace is not None else ["step1", "step2"]
        self.citations = citations if citations is not None else []
        self.rewritten_query = rewritten_query
        self.retrieved_contexts = retrieved_contexts if retrieved_contexts is not None else []
        self._custom_graph = graph
        self._stream_events = stream_events

    def get_compiled_graph(self) -> Any:
        if self._custom_graph is not None:
            return self._custom_graph

        mock = MagicMock()

        def _invoke(state: Any = None, config: Any = None, **kwargs: Any) -> Any:
            current_q = (state.get("current_query") or state.get("question")) if isinstance(state, dict) else None
            return {
                "answer": self.answer,
                "source_used": self.source_used,
                "trace": self.trace,
                "citations": self.citations,
                "retrieved_contexts": self.retrieved_contexts,
                "current_query": current_q or self.rewritten_query,
                "question": state.get("question") if isinstance(state, dict) else None,
            }

        mock.invoke.side_effect = _invoke

        async def _ainvoke(state: Any, config: Any = None, **kwargs: Any) -> Any:
            return mock.invoke(state, config=config, **kwargs)

        mock.ainvoke = _ainvoke

        async def _astream_events(state: Any, version: str = "v2", **kwargs: Any):
            if self._stream_events is not None:
                events = self._stream_events(state, version=version, **kwargs) if callable(self._stream_events) else self._stream_events
                if hasattr(events, "__aiter__"):
                    async for evt in events:
                        yield evt
                else:
                    for evt in events:
                        yield evt
            else:
                yield {
                    "event": "on_chat_model_stream",
                    "metadata": {"langgraph_node": "generate_from_kb"},
                    "data": {"chunk": MagicMock(content=self.answer, tool_call_chunks=[])},
                }

        mock.astream_events = _astream_events
        return mock

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        return {
            "question": request.question,
            "trace_id": trace_id or request.trace_id,
            "current_query": request.question,
        }

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        return ChatResponse(
            workflow=self.info.id,
            answer=final_state.get("answer") or self.answer,
            source_used=final_state.get("source_used") or self.source_used,
            trace=final_state.get("trace") or self.trace,
            citations=final_state.get("citations") or self.citations,
            rewritten_query=final_state.get("rewritten_query") or final_state.get("current_query") or final_state.get("question"),
            trace_id=trace_id or final_state.get("trace_id"),
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used") or self.source_used,
            retrieved_contexts=final_state.get("retrieved_contexts") or self.retrieved_contexts,
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "answer": f"Inference error: {exc}",
            "source_used": "direct",
            "trace": ["error_fallback"],
            "retrieved_contexts": [],
            "citations": [],
        }


@pytest.fixture(autouse=True)
def clean_registry() -> Generator[WorkflowRegistry, None, None]:
    """Autouse fixture resetting WorkflowRegistry to canonical defaults before and after each test."""
    registry = get_workflow_registry()
    registry.clear()
    register_default_workflows(registry)
    try:
        yield registry
    finally:
        registry.clear()
        register_default_workflows(registry)


@pytest.fixture
def register_mock_workflow(clean_registry: WorkflowRegistry) -> Callable[..., MockWorkflowDefinition]:
    """Helper fixture to create and register a MockWorkflowDefinition into clean_registry."""
    def _register(workflow_id: str, **kwargs: Any) -> MockWorkflowDefinition:
        wf = MockWorkflowDefinition(id=workflow_id, **kwargs)
        clean_registry.register(wf, override=True)
        return wf
    return _register


__all__ = [
    "MockWorkflowDefinition",
    "clean_registry",
    "register_mock_workflow",
]
