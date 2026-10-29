"""Unit tests for WorkflowRunner execution pipeline (Ticket 01)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import BackgroundTasks, HTTPException
import pytest
import structlog

from app.api.schemas import ChatMessage, ChatRequest, ChatResponse
from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry
from app.rag.workflows.runner import WorkflowRunner, get_workflow_runner


class MockWorkflow:
    """Mock WorkflowDefinition implementation for unit testing WorkflowRunner."""

    def __init__(self, workflow_id: str = "mock_workflow", step_labels: dict[str, str] | None = None) -> None:
        self.info = WorkflowInfo(
            id=workflow_id,
            name="Mock Workflow",
            description="Mock workflow for runner tests",
            step_labels=step_labels or {"step_1": "Executing step 1"},
            generation_nodes={"generate"},
            metadata={"domain": "testing"},
        )
        self.mock_graph = MagicMock()
        self.mock_graph.invoke.return_value = {
            "answer": "Mock Answer",
            "source_used": "vectorstore",
            "trace": ["step_1", "generate"],
            "citations": [{"title": "doc1.pdf", "url": "", "type": "private_kb"}],
            "retrieved_contexts": ["chunk context 1"],
            "current_query": "mock query",
            "trace_id": "mock-trace-id",
        }

    def get_compiled_graph(self) -> Any:
        return self.mock_graph

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        return {
            "question": request.question,
            "trace_id": trace_id or request.trace_id,
            "session_id": request.session_id,
            "retrieved_contexts": [],
            "trace": [],
        }

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        return ChatResponse(
            workflow=self.info.id,
            answer=final_state.get("answer", ""),
            source_used=final_state.get("source_used", "direct"),
            trace=final_state.get("trace", []),
            citations=final_state.get("citations", []),
            rewritten_query=final_state.get("current_query") or final_state.get("question"),
            trace_id=trace_id or final_state.get("trace_id"),
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used", "direct"),
            retrieved_contexts=final_state.get("retrieved_contexts", []),
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": f"Fallback error response: {exc}",
            "source_used": "error_fallback",
            "trace": current_state.get("trace", []) + ["error fallback"],
            "citations": [],
            "retrieved_contexts": [],
            "trace_id": current_state.get("trace_id"),
        }


@pytest.fixture(autouse=True)
def clean_contextvars():
    """Ensure structlog contextvars are pristine before and after each test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


# ==============================================================================
# 1. Factory & Injection Tests
# ==============================================================================


def test_runner_factory_and_injection():
    """Verify singleton lifecycle and injectable WorkflowRegistry constructor."""
    default_runner1 = get_workflow_runner()
    default_runner2 = get_workflow_runner()
    assert default_runner1 is default_runner2
    assert default_runner1.registry is get_workflow_registry()

    custom_registry = WorkflowRegistry()
    custom_runner = get_workflow_runner(registry=custom_registry)
    assert custom_runner is not default_runner1
    assert custom_runner.registry is custom_registry

    direct_runner = WorkflowRunner(registry=custom_registry)
    assert direct_runner.registry is custom_registry


# ==============================================================================
# 2. Workflow Lookup & Rejection
# ==============================================================================


def test_execute_unknown_workflow_raises_http_400():
    """Unknown workflow name raises HTTPException 400 with detail."""
    runner = WorkflowRunner()
    request = ChatRequest(question="What is RAG?", workflow="non_existent_wf")

    with pytest.raises(HTTPException) as exc_info:
        runner.execute(request)

    assert exc_info.value.status_code == 400
    assert "Unknown workflow: non_existent_wf" in exc_info.value.detail


@pytest.mark.asyncio
async def test_aexecute_unknown_workflow_raises_http_400():
    """Async execution of unknown workflow raises HTTPException 400."""
    runner = WorkflowRunner()
    request = ChatRequest(question="What is RAG?", workflow="non_existent_wf")

    with pytest.raises(HTTPException) as exc_info:
        await runner.aexecute(request)

    assert exc_info.value.status_code == 400
    assert "Unknown workflow: non_existent_wf" in exc_info.value.detail


# ==============================================================================
# 3. Synchronous & Asynchronous Execution
# ==============================================================================


def test_execute_registered_workflow():
    """Verify synchronous execution invokes graph and returns ChatResponse."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("test_wf")
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(
        question="How do I connect to VPN?",
        workflow="test_wf",
        session_id="sess-001",
        user_id="user-999",
    )

    response = runner.execute(request)

    assert isinstance(response, ChatResponse)
    assert response.workflow == "test_wf"
    assert response.answer == "Mock Answer"
    assert response.source_used == "vectorstore"
    assert response.citations[0]["title"] == "doc1.pdf"

    # Verify configurable thread_id passed to graph.invoke
    assert mock_wf.mock_graph.invoke.called
    call_args = mock_wf.mock_graph.invoke.call_args
    config = call_args[1].get("config") if "config" in call_args[1] else call_args[0][1]
    assert config["configurable"]["thread_id"] == "sess-001"


def test_execute_canonical_fix_single_jev_registered_workflow(monkeypatch):
    """Verify execution against canonical registered fix_single_jev workflow."""
    runner = get_workflow_runner()
    wf = runner.registry.get("fix_single_jev")

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "answer": "Canonical JEV response",
        "source_used": "vectorstore",
        "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
        "citations": [{"title": "corp_vpn.pdf", "url": "", "type": "private_kb"}],
        "current_query": "canonical query",
        "retrieved_contexts": ["retrieved context 1"],
    }
    monkeypatch.setattr(wf, "get_compiled_graph", lambda: mock_graph)

    request = ChatRequest(
        question="What is corporate policy?",
        workflow="fix_single_jev",
        session_id="sess-canon",
    )
    response = runner.execute(request)

    assert response.workflow == "fix_single_jev"
    assert response.answer == "Canonical JEV response"
    assert response.source_used == "vectorstore"
    assert len(response.citations) == 1
    assert response.citations[0]["title"] == "corp_vpn.pdf"


def test_execute_canonical_react_agent_registered_workflow(monkeypatch):
    """Verify execution against canonical registered react_agent workflow."""
    from langchain_core.messages import AIMessage, HumanMessage

    runner = get_workflow_runner()
    wf = runner.registry.get("react_agent")

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "messages": [
            HumanMessage(content="Hello"),
            AIMessage(content="Hello! I am the ReAct assistant."),
        ],
        "question": "Hello",
    }
    monkeypatch.setattr(wf, "get_compiled_graph", lambda: mock_graph)

    request = ChatRequest(
        question="Hello",
        workflow="react_agent",
        session_id="sess-react",
    )
    response = runner.execute(request)

    assert response.workflow == "react_agent"
    assert response.answer == "Hello! I am the ReAct assistant."
    assert response.source_used == "direct"


@pytest.mark.asyncio
async def test_aexecute_with_native_async_graph():
    """Verify asynchronous execution calls ainvoke if available on compiled graph."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("async_wf")

    # Add ainvoke to mock graph
    mock_wf.mock_graph.ainvoke = AsyncMock(return_value={
        "answer": "Async Answer",
        "source_used": "vectorstore",
        "trace": ["step_1"],
        "citations": [],
        "retrieved_contexts": [],
        "current_query": "async query",
    })
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Async question?", workflow="async_wf")

    response = await runner.aexecute(request)

    assert response.answer == "Async Answer"
    assert mock_wf.mock_graph.ainvoke.called


@pytest.mark.asyncio
async def test_aexecute_with_sync_graph_fallback():
    """Verify asynchronous execution falls back to threadpool invoke when graph is sync."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("sync_wf")
    # Restrict mock_graph spec so ainvoke does not exist
    mock_wf.mock_graph = MagicMock(spec=["invoke"])
    assert not hasattr(mock_wf.mock_graph, "ainvoke")
    mock_wf.mock_graph.invoke.return_value = {
        "answer": "Sync In Threadpool Answer",
        "source_used": "direct",
        "trace": [],
        "citations": [],
    }
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Sync question?", workflow="sync_wf")

    response = await runner.aexecute(request)

    assert response.answer == "Sync In Threadpool Answer"
    assert mock_wf.mock_graph.invoke.called


# ==============================================================================
# 4. Trace ID Sanitization & Contextvar Bindings
# ==============================================================================


def test_trace_id_sanitization_and_structlog_bindings():
    """Verify trace ID sanitization (strips hyphens, lowercase) and structlog bindings."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("trace_wf")
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    raw_trace_id = "TRACE-99-AbC-dEf"
    request = ChatRequest(
        question="Trace test?",
        workflow="trace_wf",
        trace_id=raw_trace_id,
        session_id="sess-trace",
        user_id="user-trace",
    )

    response = runner.execute(request)

    expected_sanitized = "trace99abcdef"
    assert response.trace_id == expected_sanitized

    # Verify contextvars were bound
    cv = structlog.contextvars.get_contextvars()
    assert cv.get("workflow") == "trace_wf"
    assert cv.get("trace_id") == expected_sanitized
    assert cv.get("session_id") == "sess-trace"
    assert cv.get("user_id") == "user-trace"

    # Verify config metadata carried trace_id
    call_args = mock_wf.mock_graph.invoke.call_args
    config = call_args[1].get("config") if "config" in call_args[1] else call_args[0][1]
    assert config["metadata"]["trace_id"] == expected_sanitized


# ==============================================================================
# 5. Error Fallback Handling
# ==============================================================================


def test_execute_error_fallback_handling():
    """Verify graph execution errors trigger workflow handle_error gracefully."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("error_wf")
    mock_wf.mock_graph.invoke.side_effect = RuntimeError("Vector DB connection timeout")
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Will this fail?", workflow="error_wf")

    response = runner.execute(request)

    assert response.source_used == "error_fallback"
    assert "Vector DB connection timeout" in response.answer
    assert "error fallback" in response.trace


@pytest.mark.asyncio
async def test_aexecute_error_fallback_handling():
    """Verify async graph execution errors trigger workflow handle_error gracefully."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("async_err_wf")
    mock_wf.mock_graph.ainvoke = AsyncMock(side_effect=ValueError("LLM provider rate limit"))
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Async fail?", workflow="async_err_wf")

    response = await runner.aexecute(request)

    assert response.source_used == "error_fallback"
    assert "LLM provider rate limit" in response.answer


# ==============================================================================
# 6. Online Evaluation Scheduling
# ==============================================================================


def test_online_evaluation_scheduled_via_background_tasks():
    """Verify online evaluation task is added to FastAPI BackgroundTasks when provided."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("eval_wf")
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    bg_tasks = BackgroundTasks()

    request = ChatRequest(
        question="How do I configure evaluation?",
        workflow="eval_wf",
        trace_id="eval-trace-123",
    )

    response = runner.execute(request, background_tasks=bg_tasks)
    assert response.workflow == "eval_wf"

    # Verify task was enqueued on BackgroundTasks
    assert len(bg_tasks.tasks) == 1
    enqueued_task = bg_tasks.tasks[0]
    assert enqueued_task.kwargs["trace_id"] == "evaltrace123"
    assert enqueued_task.kwargs["question"] == "How do I configure evaluation?"
    assert enqueued_task.kwargs["answer"] == "Mock Answer"
    assert enqueued_task.kwargs["source_used"] == "vectorstore"
    assert enqueued_task.kwargs["retrieved_contexts"] == ["chunk context 1"]


@pytest.mark.asyncio
async def test_online_evaluation_scheduled_via_asyncio_create_task():
    """Verify evaluation falls back to loop.create_task when background_tasks is None."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("eval_task_wf")
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(
        question="No BackgroundTasks?",
        workflow="eval_task_wf",
        trace_id="trace-no-bg",
    )

    response = await runner.aexecute(request, background_tasks=None)
    assert response.workflow == "eval_task_wf"

    # Verify task was registered in runner._background_tasks
    assert len(runner._background_tasks) >= 1
    tasks = list(runner._background_tasks)
    await asyncio.gather(*tasks, return_exceptions=True)


def test_online_evaluation_no_event_loop_sync_safe():
    """Verify synchronous execute without running event loop handles eval scheduling cleanly."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("sync_no_loop_wf")
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Sync no loop?", workflow="sync_no_loop_wf")

    # In regular pytest without asyncio mark, running loop may or may not exist.
    # Let's ensure running execute with background_tasks=None does not raise RuntimeError.
    response = runner.execute(request, background_tasks=None)
    assert response.answer == "Mock Answer"
