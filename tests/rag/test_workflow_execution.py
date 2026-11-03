"""Domain execution tests for WorkflowRunner event generator and lifecycle listener seams."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.evaluation import FakeEvaluator
from app.rag.memory.noop import NoOpMemoryStore
from app.rag.workflows.base import (
    EvalContext,
    WorkflowInfo,
    WorkflowInput,
    WorkflowOutput,
)
from app.rag.workflows.events import (
    ExecutionCompletedEvent,
    ExecutionFailedEvent,
    StepTransitionEvent,
    TokenDeltaEvent,
)
from app.rag.workflows.listeners import (
    BaseExecutionLifecycleListener,
    MemoryPersistenceListener,
    OnlineEvaluationListener,
)
from app.rag.workflows.registry import WorkflowRegistry
from app.rag.workflows.runner import WorkflowRunner


class DummyStreamWorkflow:
    """Mock workflow for testing pure event stream generation."""

    def __init__(self, workflow_id: str = "dummy_stream_wf") -> None:
        self.info = WorkflowInfo(
            id=workflow_id,
            name="Dummy Stream Workflow",
            description="Workflow for testing event stream emissions",
            step_labels={"step_start": "Starting node", "step_finish": "Finishing node"},
            generation_nodes={"generate"},
        )
        self.mock_graph = MagicMock()

    def get_compiled_graph(self, variant_id: str | None = None) -> Any:
        return self.mock_graph

    def to_initial_state(self, input: Any, trace_id: str | None = None) -> dict[str, Any]:
        return {
            "question": getattr(input, "question", ""),
            "trace_id": trace_id or getattr(input, "trace_id", None),
            "session_id": getattr(input, "session_id", None),
            "messages": [],
            "trace": [],
        }

    def to_workflow_output(self, final_state: dict[str, Any], trace_id: str | None = None) -> WorkflowOutput:
        return WorkflowOutput(
            workflow=self.info.id,
            answer=final_state.get("answer", ""),
            source_used=final_state.get("source_used", "direct"),
            trace=final_state.get("trace", []),
            citations=final_state.get("citations", []),
            rewritten_query=final_state.get("current_query") or final_state.get("question"),
            trace_id=trace_id or final_state.get("trace_id"),
            session_id=final_state.get("session_id"),
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used", "direct"),
            retrieved_contexts=final_state.get("retrieved_contexts", []),
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": f"Fallback error answer: {exc}",
            "source_used": "error_fallback",
            "trace": ["error_node"],
        }


class SpyExecutionLifecycleListener(BaseExecutionLifecycleListener):
    """Spy listener recording invocation lifecycle calls."""

    def __init__(self) -> None:
        self.completed_calls: list[tuple[WorkflowInput, WorkflowOutput]] = []
        self.background_calls: list[tuple[WorkflowInput, WorkflowOutput]] = []
        self.error_calls: list[tuple[WorkflowInput, Exception]] = []
        self.cancelled_calls: list[tuple[WorkflowInput, WorkflowOutput | None]] = []

    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        self.completed_calls.append((request, output))

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        self.background_calls.append((request, output))

    async def on_error(self, request: WorkflowInput, error: Exception) -> None:
        self.error_calls.append((request, error))

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        self.cancelled_calls.append((request, partial_output))


@pytest.mark.asyncio
async def test_stream_yields_typed_domain_events():
    """Verify runner.stream yields typed StepTransitionEvent, TokenDeltaEvent, and ExecutionCompletedEvent."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_events(*args: Any, **kwargs: Any):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_start"}, "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate"},
            "data": {"chunk": {"content": "Hello "}},
        }
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate"},
            "data": {"chunk": {"content": "world!"}},
        }
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_finish"}, "data": {}}
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "step_finish"},
            "data": {
                "output": {
                    "answer": "Hello world!",
                    "source_used": "direct",
                    "trace": ["step_start", "generate", "step_finish"],
                }
            },
        }

    workflow.mock_graph.astream_events = mock_events
    registry.register(workflow)

    spy_listener = SpyExecutionLifecycleListener()
    runner = WorkflowRunner(registry=registry, listeners=[spy_listener])
    request = WorkflowInput(question="Greeting test", workflow="dummy_stream_wf")

    events = [event async for event in runner.stream(request)]

    # Assert typed events
    assert len(events) == 5
    assert isinstance(events[0], StepTransitionEvent)
    assert events[0].step == "step_start"

    assert isinstance(events[1], TokenDeltaEvent)
    assert events[1].delta == "Hello "

    assert isinstance(events[2], TokenDeltaEvent)
    assert events[2].delta == "world!"

    assert isinstance(events[3], StepTransitionEvent)
    assert events[3].step == "step_finish"

    assert isinstance(events[4], ExecutionCompletedEvent)
    assert events[4].output.answer == "Hello world!"
    assert events[4].output.source_used == "direct"
    assert events[4].output.trace == ["step_start", "generate", "step_finish"]

    # Assert lifecycle listener received on_completed
    assert len(spy_listener.completed_calls) == 1
    assert spy_listener.completed_calls[0][1].answer == "Hello world!"


@pytest.mark.asyncio
async def test_stream_unhandled_exception_yields_failed_event_and_calls_on_error():
    """Verify an exception during stream triggers on_error on listeners and yields ExecutionFailedEvent."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_failing_events(*args: Any, **kwargs: Any):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_start"}, "data": {}}
        raise RuntimeError("Inference engine explosion")

    workflow.mock_graph.astream_events = mock_failing_events
    registry.register(workflow)

    spy_listener = SpyExecutionLifecycleListener()
    runner = WorkflowRunner(registry=registry, listeners=[spy_listener])
    request = WorkflowInput(question="Fail test", workflow="dummy_stream_wf")

    events = [event async for event in runner.stream(request)]

    assert len(events) == 2
    assert isinstance(events[0], StepTransitionEvent)
    assert events[0].step == "step_start"

    assert isinstance(events[1], ExecutionFailedEvent)
    assert "Inference engine explosion" in events[1].error

    # Assert on_error was invoked on listener
    assert len(spy_listener.error_calls) == 1
    assert str(spy_listener.error_calls[0][1]) == "Inference engine explosion"


@pytest.mark.asyncio
async def test_stream_cancellation_invokes_on_cancelled_and_discards_turns():
    """Verify asyncio.CancelledError invokes on_cancelled listener hook without saving incomplete turns."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_cancelled_events(*args: Any, **kwargs: Any):
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate"},
            "data": {"chunk": {"content": "Partial token"}},
        }
        raise asyncio.CancelledError()

    workflow.mock_graph.astream_events = mock_cancelled_events
    registry.register(workflow)

    spy_listener = SpyExecutionLifecycleListener()
    runner = WorkflowRunner(registry=registry, listeners=[spy_listener])
    request = WorkflowInput(question="Cancel test", workflow="dummy_stream_wf", session_id="ses-cancel")

    with pytest.raises(asyncio.CancelledError):
        _ = [event async for event in runner.stream(request)]

    assert len(spy_listener.cancelled_calls) == 1
    req, partial = spy_listener.cancelled_calls[0]
    assert req.session_id == "ses-cancel"
    if partial is not None:
        assert partial.answer == "Partial token"


@pytest.mark.asyncio
async def test_explicit_memory_none_bypasses_hydration_and_persistence():
    """Verify that when memory is None, runner never attempts hydration or persistence."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_events(*args: Any, **kwargs: Any):
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate"},
            "data": {"chunk": {"content": "Answer without memory"}},
        }
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate"},
            "data": {"output": {"answer": "Answer without memory"}},
        }

    workflow.mock_graph.astream_events = mock_events
    registry.register(workflow)

    runner = WorkflowRunner(registry=registry, memory=None)
    assert runner.memory is None

    request = WorkflowInput(question="Stateless inquiry", workflow="dummy_stream_wf", session_id="ses-123")
    events = [event async for event in runner.stream(request)]

    assert len(events) == 2
    assert isinstance(events[0], TokenDeltaEvent)
    assert isinstance(events[1], ExecutionCompletedEvent)
    assert events[1].output.answer == "Answer without memory"


@pytest.mark.asyncio
async def test_explicit_memory_noop_bypasses_hydration_and_persistence():
    """Verify that when memory is NoOpMemoryStore, runner bypasses memory operations."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_events(*args: Any, **kwargs: Any):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate"},
            "data": {"output": {"answer": "NoOp memory answer"}},
        }

    workflow.mock_graph.astream_events = mock_events
    registry.register(workflow)

    noop_store = NoOpMemoryStore()
    runner = WorkflowRunner(registry=registry, memory=noop_store)

    request = WorkflowInput(question="NoOp test", workflow="dummy_stream_wf", session_id="ses-noop")
    events = [event async for event in runner.stream(request)]

    assert len(events) == 2  # static fallback token + completed
    assert isinstance(events[0], TokenDeltaEvent)
    assert isinstance(events[1], ExecutionCompletedEvent)
    assert events[1].output.answer == "NoOp memory answer"


@pytest.mark.asyncio
async def test_default_listeners_wiring_with_memory_and_evaluator():
    """Verify default listeners build MemoryPersistenceListener and OnlineEvaluationListener when configured."""
    mock_memory = AsyncMock()
    mock_evaluator = FakeEvaluator()

    runner = WorkflowRunner(memory=mock_memory, evaluator=mock_evaluator)
    listeners = runner.listeners

    assert len(listeners) == 2
    mem_listener = next(l for l in listeners if isinstance(l, MemoryPersistenceListener))
    eval_listener = next(l for l in listeners if isinstance(l, OnlineEvaluationListener))

    assert mem_listener.memory is mock_memory
    assert eval_listener.evaluator is mock_evaluator


@pytest.mark.asyncio
async def test_default_listeners_empty_when_no_memory_or_evaluator():
    """Verify default listeners list is empty when neither memory nor evaluator are injected."""
    runner = WorkflowRunner(memory=None, evaluator=None)
    assert runner.listeners == []


@pytest.mark.asyncio
async def test_stream_generator_exit_invokes_on_cancelled():
    """Verify GeneratorExit invokes on_cancelled listener hook without saving incomplete turns."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_events(*args: Any, **kwargs: Any):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_start"}, "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate"},
            "data": {"chunk": {"content": "Token before exit"}},
        }
        raise GeneratorExit()

    workflow.mock_graph.astream_events = mock_events
    registry.register(workflow)

    spy_listener = SpyExecutionLifecycleListener()
    runner = WorkflowRunner(registry=registry, listeners=[spy_listener])
    request = WorkflowInput(question="GenExit test", workflow="dummy_stream_wf", session_id="ses-exit")

    with pytest.raises(GeneratorExit):
        _ = [event async for event in runner.stream(request)]

    assert len(spy_listener.cancelled_calls) == 1
    req, partial = spy_listener.cancelled_calls[0]
    assert req.session_id == "ses-exit"
    if partial is not None:
        assert partial.answer == "Token before exit"


@pytest.mark.asyncio
async def test_stream_honors_stream_steps_false():
    """Verify setting stream_steps=False suppresses StepTransitionEvent emissions."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_events(*args: Any, **kwargs: Any):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_start"}, "data": {}}
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

    workflow.mock_graph.astream_events = mock_events
    registry.register(workflow)

    runner = WorkflowRunner(registry=registry)
    request = WorkflowInput(question="No steps test", workflow="dummy_stream_wf", stream_steps=False)

    events = [event async for event in runner.stream(request)]
    step_events = [e for e in events if isinstance(e, StepTransitionEvent)]
    assert len(step_events) == 0

    token_events = [e for e in events if isinstance(e, TokenDeltaEvent)]
    assert len(token_events) == 1
    assert token_events[0].delta == "Direct token."


@pytest.mark.asyncio
async def test_memory_persistence_listener_persists_turn():
    """Verify MemoryPersistenceListener automatically saves completed turns via runner.stream."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_events(*args: Any, **kwargs: Any):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate"},
            "data": {"output": {"answer": "Memory persisted answer", "source_used": "rag"}},
        }

    workflow.mock_graph.astream_events = mock_events
    registry.register(workflow)

    mock_memory = AsyncMock()
    runner = WorkflowRunner(registry=registry, memory=mock_memory)

    request = WorkflowInput(question="Persistence test", workflow="dummy_stream_wf", session_id="ses-persist-1")
    events = [event async for event in runner.stream(request)]

    assert len(events) == 2  # static fallback token + completed
    assert isinstance(events[1], ExecutionCompletedEvent)

    # Verify aappend_turn was called on injected memory
    assert mock_memory.aappend_turn.called
    kwargs = mock_memory.aappend_turn.call_args[1]
    assert kwargs["session_id"] == "ses-persist-1"
    assert kwargs["assistant_answer"] == "Memory persisted answer"
    assert kwargs["user_query"] == "Persistence test"


@pytest.mark.asyncio
async def test_listener_exception_is_shielded_and_does_not_abort_stream():
    """Verify that an exception raised inside a lifecycle listener hook does not break stream completion."""
    registry = WorkflowRegistry()
    workflow = DummyStreamWorkflow()

    async def mock_events(*args: Any, **kwargs: Any):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate"},
            "data": {"output": {"answer": "Shielded listener test"}},
        }

    workflow.mock_graph.astream_events = mock_events
    registry.register(workflow)

    failing_listener = BaseExecutionLifecycleListener()
    failing_listener.on_completed = AsyncMock(side_effect=RuntimeError("Listener webhook down!"))

    runner = WorkflowRunner(registry=registry, listeners=[failing_listener])
    request = WorkflowInput(question="Shield test", workflow="dummy_stream_wf")

    events = [event async for event in runner.stream(request)]
    assert len(events) == 2
    assert isinstance(events[1], ExecutionCompletedEvent)
    assert events[1].output.answer == "Shielded listener test"
