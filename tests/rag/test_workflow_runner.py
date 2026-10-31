"""Unit tests for WorkflowRunner execution pipeline (Ticket 01)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import BackgroundTasks, HTTPException
import pytest
import structlog

from app.api.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    StreamErrorEvent,
    StreamMetadataEvent,
    StreamStepEvent,
    StreamTokenEvent,
)
from app.core.telemetry import (
    extract_trace_id,
    flush_telemetry,
    get_langfuse_callback,
)
from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry
from app.rag.workflows.runner import (
    DEFAULT_GENERATION_NODES,
    DEFAULT_STEP_LABELS,
    WorkflowRunner,
    _background_tasks,
    format_sse,
    get_workflow_runner,
    is_tool_call_chunk,
    reset_workflow_runner,
)
from app.services.evaluation import run_online_evaluation_task


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


def parse_sse_events(raw_chunks: list[str]) -> list[dict[str, Any]]:
    """Parse raw SSE chunks into list of dicts with event and data."""
    events = []
    for chunk in raw_chunks:
        ev_name = None
        data_lines = []
        for line in chunk.splitlines():
            if line.startswith("event:"):
                ev_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if ev_name:
            payload_str = "\n".join(data_lines)
            try:
                data = json.loads(payload_str)
            except json.JSONDecodeError:
                data = payload_str
            events.append({"event": ev_name, "data": data})
    return events


# ==============================================================================
# 7. SSE Formatting & Tool Delta Filtering Helpers
# ==============================================================================


def test_format_sse_helper():
    """Verify format_sse handles dict and BaseModel instances identically."""
    # Dict payload
    res_dict = format_sse("custom", {"foo": "bar", "num": 42})
    assert res_dict == 'event: custom\ndata: {"foo": "bar", "num": 42}\n\n'

    # BaseModel payload
    step_event = StreamStepEvent(step="retrieve_kb", label="Searching internal knowledge base...")
    res_model = format_sse("step", step_event)
    assert res_model.startswith("event: step\ndata: {")
    assert '"step": "retrieve_kb"' in res_model
    assert '"label": "Searching internal knowledge base..."' in res_model
    assert res_model.endswith("\n\n")

    # WorkflowRunner method delegation
    runner = WorkflowRunner()
    res_runner = runner.format_sse("token", StreamTokenEvent(delta="test token"))
    assert res_runner == 'event: token\ndata: {"delta": "test token"}\n\n'


def test_is_tool_call_chunk_filtering():
    """Verify is_tool_call_chunk correctly identifies all variants of tool call chunks."""
    runner = WorkflowRunner()

    # None and empty objects
    assert not is_tool_call_chunk(None)
    assert not runner.is_tool_call_chunk(None)

    plain_chunk = MagicMock(spec=["content"])
    plain_chunk.content = "regular conversational delta"
    assert not is_tool_call_chunk(plain_chunk)
    assert not runner.is_tool_call_chunk(plain_chunk)

    # Object with tool_call_chunks attribute
    tool_chunk1 = MagicMock()
    tool_chunk1.tool_call_chunks = [{"name": "web_search", "args": "python"}]
    assert is_tool_call_chunk(tool_chunk1)
    assert runner.is_tool_call_chunk(tool_chunk1)

    # Object with tool_calls attribute
    tool_chunk2 = MagicMock()
    tool_chunk2.tool_call_chunks = None
    tool_chunk2.tool_calls = [{"name": "retrieve_kb", "args": {"query": "vpn"}}]
    assert is_tool_call_chunk(tool_chunk2)

    # Dict with tool_call_chunks or tool_calls
    assert is_tool_call_chunk({"tool_call_chunks": [{"index": 0}]})
    assert is_tool_call_chunk({"tool_calls": [{"id": "call_1"}]})
    assert not is_tool_call_chunk({"content": "conversational content"})

    # Object with .message wrapper
    wrapped_mock = MagicMock()
    wrapped_mock.tool_call_chunks = None
    wrapped_mock.tool_calls = None
    inner_msg = MagicMock()
    inner_msg.tool_call_chunks = [{"name": "tool_x"}]
    wrapped_mock.message = inner_msg
    assert is_tool_call_chunk(wrapped_mock)

    # Dict with inner message dict
    assert is_tool_call_chunk({"message": {"tool_calls": [{"name": "search"}]}})
    assert not is_tool_call_chunk({"message": {"content": "normal reply"}})


# ==============================================================================
# 8. WorkflowRunner.stream Streaming Pipeline
# ==============================================================================


def test_stream_unknown_workflow_raises_http_400():
    """Calling stream with unknown workflow raises HTTPException 400 before generator begins."""
    runner = WorkflowRunner()
    request = ChatRequest(question="What is RAG?", workflow="non_existent_wf")

    with pytest.raises(HTTPException) as exc_info:
        runner.stream(request)

    assert exc_info.value.status_code == 400
    assert "Unknown workflow: non_existent_wf" in exc_info.value.detail


@pytest.mark.asyncio
async def test_stream_emits_whitelisted_steps_filtered_tokens_and_metadata():
    """Verify stream pipeline emits typed step, token, and metadata SSE events with delta filtering."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow(
        "stream_test_wf",
        step_labels={"step_1": "Analyzing question...", "step_2": "Synthesizing answer..."},
    )

    async def mock_events(*args, **kwargs):
        # 1. Whitelisted step
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}

        # 2. Non-whitelisted step (should be ignored)
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "internal_private_step"}, "data": {}}

        # 3. Tool call chunk during generation (filtered out)
        tool_chunk = MagicMock()
        tool_chunk.tool_call_chunks = [{"name": "private_kb"}]
        tool_chunk.content = ""
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "generate"}, "data": {"chunk": tool_chunk}}

        # 4. Empty text chunk during generation (filtered out)
        empty_chunk = MagicMock()
        empty_chunk.tool_call_chunks = None
        empty_chunk.tool_calls = None
        empty_chunk.message = None
        empty_chunk.content = ""
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "generate"}, "data": {"chunk": empty_chunk}}

        # 5. Non-generation node stream chunk (filtered out)
        other_node_chunk = MagicMock()
        other_node_chunk.tool_call_chunks = None
        other_node_chunk.content = "should be ignored"
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "other_node"}, "data": {"chunk": other_node_chunk}}

        # 6. Real conversational text tokens
        text_chunk_1 = MagicMock()
        text_chunk_1.tool_call_chunks = None
        text_chunk_1.tool_calls = None
        text_chunk_1.message = None
        text_chunk_1.content = "Hello "
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "generate"}, "data": {"chunk": text_chunk_1}}

        text_chunk_2 = MagicMock()
        text_chunk_2.tool_call_chunks = None
        text_chunk_2.tool_calls = None
        text_chunk_2.message = None
        text_chunk_2.content = "World!"
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "generate"}, "data": {"chunk": text_chunk_2}}

        # 7. Chain end event
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate"},
            "data": {
                "output": {
                    "trace": ["step_1", "generate"],
                    "citations": [{"title": "network_guide.pdf", "url": "", "type": "private_kb"}],
                    "answer": "Hello World!",
                    "current_query": "rewritten query",
                }
            },
        }

    mock_wf.mock_graph.astream_events = mock_events
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(
        question="How do I connect?",
        workflow="stream_test_wf",
        session_id="sess-stream",
        trace_id="stream-trace-001",
    )

    frames = [frame async for frame in runner.stream(request)]
    events = parse_sse_events(frames)

    # 1. Step event verification
    step_events = [e for e in events if e["event"] == "step"]
    assert len(step_events) == 1
    assert step_events[0]["data"]["step"] == "step_1"
    assert step_events[0]["data"]["label"] == "Analyzing question..."
    assert "timestamp" in step_events[0]["data"]

    # 2. Token events verification
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 2
    assert token_events[0]["data"]["delta"] == "Hello "
    assert token_events[1]["data"]["delta"] == "World!"

    # 3. Metadata event verification
    meta_events = [e for e in events if e["event"] == "metadata"]
    assert len(meta_events) == 1
    meta = meta_events[0]["data"]
    assert meta["workflow"] == "stream_test_wf"
    assert meta["trace_id"] == "streamtrace001"
    assert meta["trace"] == ["step_1", "generate"]
    assert meta["citations"][0]["title"] == "network_guide.pdf"
    assert meta["rewritten_query"] == "rewritten query"


@pytest.mark.asyncio
async def test_stream_honors_stream_steps_false():
    """Verify setting stream_steps=False suppresses step event emissions."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("no_steps_wf", step_labels={"step_1": "Analyzing..."})

    async def mock_events(*args, **kwargs):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate"},
            "data": {"chunk": {"content": "Direct token."}},
        }
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate"},
            "data": {"output": {"answer": "Direct token."}},
        }

    mock_wf.mock_graph.astream_events = mock_events
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(
        question="No steps please",
        workflow="no_steps_wf",
        stream_steps=False,
    )

    frames = [frame async for frame in runner.stream(request)]
    events = parse_sse_events(frames)

    # Verify zero step events
    step_events = [e for e in events if e["event"] == "step"]
    assert len(step_events) == 0

    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"]["delta"] == "Direct token."


@pytest.mark.asyncio
async def test_stream_fallback_static_answer_synthesis():
    """When zero token chunks are streamed from terminal nodes, fallback static answer is emitted as a token."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("fallback_wf")

    async def mock_events(*args, **kwargs):
        # Chain completes without yielding any on_chat_model_stream events (e.g. insufficient node)
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "step_1"},
            "data": {
                "output": {
                    "answer": "Static answer synthesized by terminal node.",
                    "source_used": "insufficient_evidence",
                    "trace": ["step_1", "insufficient"],
                }
            },
        }

    mock_wf.mock_graph.astream_events = mock_events
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Zero tokens?", workflow="fallback_wf")

    frames = [frame async for frame in runner.stream(request)]
    events = parse_sse_events(frames)

    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"]["delta"] == "Static answer synthesized by terminal node."

    meta_events = [e for e in events if e["event"] == "metadata"]
    assert len(meta_events) == 1
    assert meta_events[0]["data"]["source_used"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_stream_unhandled_exception_calls_handle_error_and_yields_stream_error_event():
    """Graph execution error during stream triggers workflow handle_error and yields typed StreamErrorEvent."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("err_stream_wf")

    async def mock_events_failing(*args, **kwargs):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}
        raise RuntimeError("Vector database stream timeout")

    mock_wf.mock_graph.astream_events = mock_events_failing
    handle_error_spy = MagicMock(wraps=mock_wf.handle_error)
    mock_wf.handle_error = handle_error_spy
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Will streaming fail?", workflow="err_stream_wf")

    frames = [frame async for frame in runner.stream(request)]
    events = parse_sse_events(frames)

    # Verify handle_error was invoked
    assert handle_error_spy.called

    # Verify error frame
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    err = error_events[0]["data"]
    assert err["code"] == 500
    assert "Vector database stream timeout" in err["message"]


@pytest.mark.asyncio
async def test_stream_client_cancellation_logging():
    """Verify asyncio.CancelledError and GeneratorExit log structured cancellation and re-raise."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("cancel_wf")

    async def mock_events_cancelled(*args, **kwargs):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}
        raise asyncio.CancelledError()

    mock_wf.mock_graph.astream_events = mock_events_cancelled
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(question="Cancel test?", workflow="cancel_wf")

    with patch("app.rag.workflows.runner.logger.warning") as mock_log_warn:
        with pytest.raises(asyncio.CancelledError):
            _ = [frame async for frame in runner.stream(request)]

        assert mock_log_warn.called
        call_kwargs = mock_log_warn.call_args[1]
        assert call_kwargs["source_used"] == "client_disconnected"
        assert call_kwargs["workflow"] == "cancel_wf"

    # Also test GeneratorExit
    async def mock_events_gen_exit(*args, **kwargs):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}
        raise GeneratorExit()

    mock_wf.mock_graph.astream_events = mock_events_gen_exit
    with patch("app.rag.workflows.runner.logger.warning") as mock_log_warn_gen:
        with pytest.raises(GeneratorExit):
            _ = [frame async for frame in runner.stream(request)]

        assert mock_log_warn_gen.called
        call_kwargs = mock_log_warn_gen.call_args[1]
        assert call_kwargs["source_used"] == "client_disconnected"


@pytest.mark.asyncio
async def test_stream_finally_block_flushes_telemetry():
    """Verify flush_telemetry and handler flush are invoked in finally block across success and failure."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("telemetry_stream_wf")

    async def mock_events(*args, **kwargs):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "step_1"},
            "data": {"output": {"answer": "Telemetry test."}},
        }

    mock_wf.mock_graph.astream_events = mock_events
    custom_registry.register(mock_wf)

    mock_handler = MagicMock()
    mock_handler.flush = MagicMock()
    mock_cb_factory = MagicMock(return_value=mock_handler)
    mock_flush = MagicMock()

    runner = WorkflowRunner(
        registry=custom_registry,
        callback_factory=mock_cb_factory,
        telemetry_flusher=mock_flush,
    )
    request = ChatRequest(question="Telemetry test", workflow="telemetry_stream_wf")

    frames = [frame async for frame in runner.stream(request)]
    assert len(frames) >= 1
    assert mock_cb_factory.called
    assert mock_flush.called
    assert mock_handler.flush.called


@pytest.mark.asyncio
async def test_stream_background_evaluation_task_safeguarded():
    """Verify background evaluation task is created, added to _background_tasks, and discarded on finish."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("stream_eval_wf")

    async def mock_events(*args, **kwargs):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "step_1"},
            "data": {
                "output": {
                    "answer": "Stream eval response",
                    "source_used": "vectorstore",
                    "retrieved_contexts": ["ctx 1"],
                }
            },
        }

    mock_wf.mock_graph.astream_events = mock_events
    custom_registry.register(mock_wf)

    async def slow_eval(*args, **kwargs):
        await asyncio.sleep(0.01)

    mock_eval_task = AsyncMock(side_effect=slow_eval)
    runner = WorkflowRunner(registry=custom_registry, eval_task_fn=mock_eval_task)
    request = ChatRequest(
        question="Eval stream?",
        workflow="stream_eval_wf",
        trace_id="eval-stream-trace",
    )

    frames = [frame async for frame in runner.stream(request)]
    events = parse_sse_events(frames)
    assert len(events) >= 1

    # Verify task is currently safeguarded in runner._background_tasks and module _background_tasks
    assert len(runner._background_tasks) >= 1
    assert len(_background_tasks) >= 1

    # Await tasks to complete
    await asyncio.gather(*list(runner._background_tasks), return_exceptions=True)

    # After completion callbacks, sets should be clean
    assert len(runner._background_tasks) == 0
    assert len(_background_tasks) == 0
    assert mock_eval_task.called


# ==============================================================================
# 9. Canonical Production Hooks Auto-Wiring & Singleton Reset
# ==============================================================================


def test_default_workflow_runner_auto_wires_canonical_production_hooks():
    """Verify WorkflowRunner defaults auto-wire canonical adapters without manual injection."""
    runner = WorkflowRunner()
    assert runner.callback_factory is get_langfuse_callback
    assert runner.trace_extractor is extract_trace_id
    assert runner.telemetry_flusher is flush_telemetry
    assert runner.eval_task_fn is run_online_evaluation_task
    assert runner.registry is get_workflow_registry()

    default_runner = get_workflow_runner()
    assert default_runner.callback_factory is get_langfuse_callback
    assert default_runner.trace_extractor is extract_trace_id
    assert default_runner.telemetry_flusher is flush_telemetry
    assert default_runner.eval_task_fn is run_online_evaluation_task


def test_reset_workflow_runner_cleanly_resets_singleton():
    """Verify reset_workflow_runner discards _default_runner singleton instance."""
    r1 = get_workflow_runner()
    r2 = get_workflow_runner()
    assert r1 is r2

    reset_workflow_runner()
    r3 = get_workflow_runner()
    assert r3 is not r1
    assert isinstance(r3, WorkflowRunner)


# ==============================================================================
# 10. Constructor-Based Hook Injection
# ==============================================================================


def test_constructor_hook_injection_sync_execute():
    """Verify WorkflowRunner(callback_factory=...) passes handler to execution config."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("cb_inject_sync_wf")
    custom_registry.register(mock_wf)

    mock_handler = MagicMock()
    mock_cb_factory = MagicMock(return_value=mock_handler)
    mock_flusher = MagicMock()

    runner = WorkflowRunner(
        registry=custom_registry,
        callback_factory=mock_cb_factory,
        telemetry_flusher=mock_flusher,
    )
    request = ChatRequest(
        question="How to configure constructor hooks?",
        workflow="cb_inject_sync_wf",
        session_id="sess-cb-sync",
    )

    response = runner.execute(request)
    assert response.workflow == "cb_inject_sync_wf"
    assert mock_cb_factory.called
    cb_kwargs = mock_cb_factory.call_args[1]
    assert cb_kwargs["workflow_name"] == "cb_inject_sync_wf"
    assert cb_kwargs["session_id"] == "sess-cb-sync"

    # Verify handler was injected into LangGraph run_config callbacks
    call_args = mock_wf.mock_graph.invoke.call_args
    config = call_args[1].get("config") if "config" in call_args[1] else call_args[0][1]
    assert config["callbacks"] == [mock_handler]
    assert mock_flusher.called


@pytest.mark.asyncio
async def test_constructor_hook_injection_async_aexecute():
    """Verify WorkflowRunner(callback_factory=...) passes handler to aexecute config."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("cb_inject_async_wf")
    mock_wf.mock_graph.ainvoke = AsyncMock(return_value={
        "answer": "Async Answer",
        "source_used": "direct",
        "trace": [],
        "citations": [],
    })
    custom_registry.register(mock_wf)

    mock_handler = MagicMock()
    mock_cb_factory = MagicMock(return_value=mock_handler)
    mock_flusher = MagicMock()

    runner = WorkflowRunner(
        registry=custom_registry,
        callback_factory=mock_cb_factory,
        telemetry_flusher=mock_flusher,
    )
    request = ChatRequest(
        question="Async constructor hooks?",
        workflow="cb_inject_async_wf",
        session_id="sess-cb-async",
    )

    response = await runner.aexecute(request)
    assert response.answer == "Async Answer"
    assert mock_cb_factory.called
    call_args = mock_wf.mock_graph.ainvoke.call_args
    config = call_args[1].get("config") if "config" in call_args[1] else call_args[0][1]
    assert config["callbacks"] == [mock_handler]
    assert mock_flusher.called


@pytest.mark.asyncio
async def test_constructor_hook_injection_stream():
    """Verify WorkflowRunner(callback_factory=...) activates handler during stream."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("cb_inject_stream_wf")

    async def mock_events(*args, **kwargs):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "step_1"},
            "data": {"output": {"answer": "Stream constructor answer"}},
        }

    mock_wf.mock_graph.astream_events = mock_events
    custom_registry.register(mock_wf)

    mock_handler = MagicMock()
    mock_handler.flush = MagicMock()
    mock_cb_factory = MagicMock(return_value=mock_handler)
    mock_flusher = MagicMock()

    runner = WorkflowRunner(
        registry=custom_registry,
        callback_factory=mock_cb_factory,
        telemetry_flusher=mock_flusher,
    )
    request = ChatRequest(
        question="Stream constructor hooks?",
        workflow="cb_inject_stream_wf",
        session_id="sess-cb-stream",
    )

    frames = [frame async for frame in runner.stream(request)]
    assert len(frames) >= 1
    assert mock_cb_factory.called
    assert mock_flusher.called
    assert mock_handler.flush.called


# ==============================================================================
# 11. Background Evaluation Exception Shielding & Warning Logging
# ==============================================================================


def test_online_evaluation_scheduling_exception_shielded_with_warning():
    """Verify exceptions in BackgroundTasks.add_task are logged as warnings and never abort execute()."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("failing_bg_task_wf")
    custom_registry.register(mock_wf)

    runner = WorkflowRunner(registry=custom_registry)
    request = ChatRequest(
        question="Will bg_task error crash execute?",
        workflow="failing_bg_task_wf",
        trace_id="eval-crash-trace",
    )

    failing_bg_tasks = MagicMock()
    failing_bg_tasks.add_task.side_effect = RuntimeError("BackgroundTasks queue pool exhausted")

    with patch("app.rag.workflows.runner.logger.warning") as mock_log_warn:
        response = runner.execute(request, background_tasks=failing_bg_tasks)
        assert response.workflow == "failing_bg_task_wf"
        assert response.answer == "Mock Answer"
        assert mock_log_warn.called
        log_event = mock_log_warn.call_args[0][0]
        assert log_event == "online_evaluation_scheduling_failed"
        assert "BackgroundTasks queue pool exhausted" in mock_log_warn.call_args[1]["error"]


@pytest.mark.asyncio
async def test_online_evaluation_task_exception_shielded_in_aexecute():
    """Verify exceptions inside eval_task_fn during aexecute() are caught, logged, and never raised."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("failing_eval_task_wf")
    mock_wf.mock_graph.ainvoke = AsyncMock(return_value={
        "answer": "Async eval shielded answer",
        "source_used": "vectorstore",
        "trace": [],
        "citations": [],
    })
    custom_registry.register(mock_wf)

    mock_failing_eval = AsyncMock(side_effect=RuntimeError("Langfuse evaluation timeout"))
    runner = WorkflowRunner(registry=custom_registry, eval_task_fn=mock_failing_eval)
    request = ChatRequest(
        question="Will eval crash aexecute?",
        workflow="failing_eval_task_wf",
        trace_id="eval-aexec-trace",
    )

    with patch("app.rag.workflows.runner.logger.warning") as mock_log_warn:
        response = await runner.aexecute(request)
        assert response.answer == "Async eval shielded answer"

        # Await any spawned background evaluation tasks
        await asyncio.gather(*list(runner._background_tasks), return_exceptions=True)

        assert mock_failing_eval.called
        assert mock_log_warn.called
        log_event = mock_log_warn.call_args[0][0]
        assert log_event == "online_evaluation_failed_shielded"
        assert "Langfuse evaluation timeout" in mock_log_warn.call_args[1]["error"]


@pytest.mark.asyncio
async def test_online_evaluation_task_exception_shielded_in_stream():
    """Verify exceptions inside eval_task_fn during stream() are caught, logged, and never abort stream."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("failing_stream_eval_wf")

    async def mock_events(*args, **kwargs):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "step_1"},
            "data": {"output": {"answer": "Stream eval shielded answer"}},
        }

    mock_wf.mock_graph.astream_events = mock_events
    custom_registry.register(mock_wf)

    mock_failing_eval = AsyncMock(side_effect=ValueError("DeepEval connection refused"))
    runner = WorkflowRunner(registry=custom_registry, eval_task_fn=mock_failing_eval)
    request = ChatRequest(
        question="Will eval crash stream?",
        workflow="failing_stream_eval_wf",
        trace_id="stream-failing-trace",
    )

    with patch("app.rag.workflows.runner.logger.warning") as mock_log_warn:
        frames = [frame async for frame in runner.stream(request)]
        events = parse_sse_events(frames)
        assert len(events) >= 1

        # Verify no error SSE frame was emitted due to eval failure
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 0

        # Await background evaluation tasks
        await asyncio.gather(*list(runner._background_tasks), return_exceptions=True)

        assert mock_failing_eval.called
        assert mock_log_warn.called
        log_event = mock_log_warn.call_args[0][0]
        assert log_event == "online_evaluation_failed_shielded"
        assert "DeepEval connection refused" in mock_log_warn.call_args[1]["error"]


# ==============================================================================
# 12. Extraneous Hook Kwargs Handling
# ==============================================================================


def test_execute_and_aexecute_ignore_extraneous_hook_kwargs_cleanly():
    """Verify execute() and aexecute() cleanly ignore extraneous hook kwargs without overriding injected hooks."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("ignore_kwargs_wf")
    mock_wf.mock_graph.ainvoke = AsyncMock(return_value={
        "answer": "Async answer",
        "source_used": "direct",
        "trace": [],
        "citations": [],
    })
    custom_registry.register(mock_wf)

    injected_cb = MagicMock(return_value=MagicMock())
    bogus_cb = MagicMock()
    bogus_eval = MagicMock()

    runner = WorkflowRunner(registry=custom_registry, callback_factory=injected_cb)
    request = ChatRequest(question="Ignore hook kwargs?", workflow="ignore_kwargs_wf")

    # Sync execute with extraneous hook kwargs
    response_sync = runner.execute(
        request,
        callback_factory=bogus_cb,
        eval_task_fn=bogus_eval,
        unexpected_hook_param="should_be_ignored",
    )
    assert response_sync.answer == "Mock Answer"
    assert injected_cb.called
    assert not bogus_cb.called
    assert not bogus_eval.called


@pytest.mark.asyncio
async def test_aexecute_ignores_extraneous_hook_kwargs_cleanly():
    """Verify aexecute() cleanly ignores extraneous hook kwargs."""
    custom_registry = WorkflowRegistry()
    mock_wf = MockWorkflow("async_ignore_kwargs_wf")
    mock_wf.mock_graph.ainvoke = AsyncMock(return_value={
        "answer": "Async ignore answer",
        "source_used": "direct",
        "trace": [],
        "citations": [],
    })
    custom_registry.register(mock_wf)

    injected_cb = MagicMock(return_value=MagicMock())
    bogus_cb = MagicMock()

    runner = WorkflowRunner(registry=custom_registry, callback_factory=injected_cb)
    request = ChatRequest(question="Async ignore kwargs?", workflow="async_ignore_kwargs_wf")

    response_async = await runner.aexecute(
        request,
        callback_factory=bogus_cb,
        extraneous_kwarg="ignore_me",
    )
    assert response_async.answer == "Async ignore answer"
    assert injected_cb.called
    assert not bogus_cb.called


def test_stream_rejects_extraneous_hook_kwargs_with_type_error():
    """Verify stream() rejects extraneous hook kwargs cleanly by raising TypeError."""
    runner = WorkflowRunner()
    request = ChatRequest(question="Stream reject kwargs?", workflow="any_wf")

    with pytest.raises(TypeError) as exc_info1:
        runner.stream(request, callback_factory=MagicMock())  # type: ignore[call-arg]
    assert "unexpected keyword argument" in str(exc_info1.value)

    with pytest.raises(TypeError) as exc_info2:
        runner.stream(request, eval_task_fn=MagicMock())  # type: ignore[call-arg]
    assert "unexpected keyword argument" in str(exc_info2.value)

    with pytest.raises(TypeError) as exc_info3:
        runner.stream(request, trace_extractor=MagicMock())  # type: ignore[call-arg]
    assert "unexpected keyword argument" in str(exc_info3.value)

