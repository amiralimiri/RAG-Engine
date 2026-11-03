"""Unit tests for WorkflowRunner._adispatch_listeners and _dispatch_listeners (Ticket 01)."""

from __future__ import annotations

import asyncio

import pytest

from app.rag.workflows.base import WorkflowInput, WorkflowOutput
from app.rag.workflows.listeners import BaseExecutionLifecycleListener
from app.rag.workflows.runner import WorkflowRunner, _background_tasks


class SpyListener(BaseExecutionLifecycleListener):
    """Spy listener tracking all lifecycle invocations."""

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


class FailingListener(BaseExecutionLifecycleListener):
    """Failing listener that raises exceptions in all hooks."""

    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        raise RuntimeError("FailingListener.on_completed explosion")

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        raise RuntimeError("FailingListener.on_background_task explosion")

    async def on_error(self, request: WorkflowInput, error: Exception) -> None:
        raise RuntimeError("FailingListener.on_error explosion")

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        raise RuntimeError("FailingListener.on_cancelled explosion")


@pytest.mark.asyncio
async def test_adispatch_listeners_completed_invokes_hooks_and_shields_bg_tasks() -> None:
    """Verify _adispatch_listeners invokes on_completed and schedules on_background_task."""
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[spy])

    request = WorkflowInput(question="Test question?", session_id="ses-123")
    output = WorkflowOutput(
        workflow="mock_wf",
        answer="Test answer",
        source_used="kb",
        retrieved_contexts=["chunk 1", "chunk 2"],
    )

    await runner._adispatch_listeners("completed", request, output=output)

    # on_completed should have been awaited
    assert len(spy.completed_calls) == 1
    assert spy.completed_calls[0] == (request, output)

    # Let scheduled background task run on event loop
    await asyncio.sleep(0.01)

    assert len(spy.background_calls) == 1
    assert spy.background_calls[0] == (request, output)


@pytest.mark.asyncio
async def test_adispatch_listeners_normalizes_retrieved_contexts() -> None:
    """Verify retrieved_contexts is normalized to a list of strings before notifying listeners."""
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[spy])

    request = WorkflowInput(question="Normalizing test")
    # Output constructed without strict model validation to test defensive normalization
    output = WorkflowOutput.model_construct(
        workflow="mock_wf",
        answer="Normalized answer",
        retrieved_contexts=[
            {"content": "chunk from dict content"},
            {"text": "chunk from dict text"},
            {"snippet": "chunk from dict snippet"},
            {"page_content": "chunk from dict page_content"},
            {"custom_meta": 123},
            "chunk string",
            12345,
        ],
    )

    await runner._adispatch_listeners("completed", request, output=output)

    assert len(spy.completed_calls) == 1
    received_output = spy.completed_calls[0][1]
    assert received_output.retrieved_contexts == [
        "chunk from dict content",
        "chunk from dict text",
        "chunk from dict snippet",
        "chunk from dict page_content",
        "{'custom_meta': 123}",
        "chunk string",
        "12345",
    ]


@pytest.mark.asyncio
async def test_adispatch_listeners_normalizes_none_retrieved_contexts() -> None:
    """Verify None retrieved_contexts is normalized to empty list."""
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[spy])

    request = WorkflowInput(question="Normalizing none test")
    output = WorkflowOutput(
        workflow="mock_wf",
        answer="Normalized answer",
    )
    # Force retrieved_contexts to None to verify defensive normalization
    output.retrieved_contexts = None  # type: ignore

    await runner._adispatch_listeners("completed", request, output=output)

    assert len(spy.completed_calls) == 1
    received_output = spy.completed_calls[0][1]
    assert received_output.retrieved_contexts == []


def test_workflow_output_normalize_contexts_direct():
    """Verify WorkflowOutput.normalize_contexts normalizes dicts, strings, and non-string chunks."""
    output = WorkflowOutput.model_construct(
        workflow="mock_wf",
        answer="Test answer",
        retrieved_contexts=[
            {"content": "c1"},
            {"text": "c2"},
            {"snippet": "c3"},
            {"page_content": "c4"},
            "raw string",
            999,
        ],
    )
    res = output.normalize_contexts()
    assert res == ["c1", "c2", "c3", "c4", "raw string", "999"]
    assert output.retrieved_contexts == ["c1", "c2", "c3", "c4", "raw string", "999"]

    output_none = WorkflowOutput.model_construct(
        workflow="mock_wf",
        answer="Test answer",
        retrieved_contexts=None,
    )
    assert output_none.normalize_contexts() == []
    assert output_none.retrieved_contexts == []


@pytest.mark.asyncio
async def test_adispatch_listeners_fail_open_isolation() -> None:
    """Verify failing listener does not prevent subsequent listeners from executing."""
    failing = FailingListener()
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[failing, spy])

    request = WorkflowInput(question="Fail-open test")
    output = WorkflowOutput(
        workflow="mock_wf",
        answer="Fail-open answer",
        retrieved_contexts=["ctx"],
    )

    # Should not raise exception despite failing listener
    await runner._adispatch_listeners("completed", request, output=output)

    assert len(spy.completed_calls) == 1
    assert spy.completed_calls[0] == (request, output)

    await asyncio.sleep(0.01)
    assert len(spy.background_calls) == 1
    assert spy.background_calls[0] == (request, output)


@pytest.mark.asyncio
async def test_adispatch_listeners_error_event_with_fail_open() -> None:
    """Verify error event dispatches on_error sequentially with fail-open isolation."""
    failing = FailingListener()
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[failing, spy])

    request = WorkflowInput(question="Error test")
    test_err = RuntimeError("Graph execution boom")

    await runner._adispatch_listeners("error", request, error=test_err)

    assert len(spy.error_calls) == 1
    assert spy.error_calls[0] == (request, test_err)


@pytest.mark.asyncio
async def test_adispatch_listeners_cancelled_event_with_fail_open() -> None:
    """Verify cancelled event dispatches on_cancelled sequentially with fail-open isolation."""
    failing = FailingListener()
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[failing, spy])

    request = WorkflowInput(question="Cancelled test")
    partial_output = WorkflowOutput(workflow="mock_wf", answer="Partial...")

    await runner._adispatch_listeners("cancelled", request, output=partial_output)

    assert len(spy.cancelled_calls) == 1
    assert spy.cancelled_calls[0] == (request, partial_output)


@pytest.mark.asyncio
async def test_adispatch_listeners_background_task_tracking_and_gc_shielding() -> None:
    """Verify background tasks are added to both global and instance tracking sets and discarded upon completion."""
    task_started = asyncio.Event()
    task_finish = asyncio.Event()

    class LongRunningBgListener(BaseExecutionLifecycleListener):
        async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
            task_started.set()
            await task_finish.wait()

    bg_listener = LongRunningBgListener()
    runner = WorkflowRunner(listeners=[bg_listener])

    request = WorkflowInput(question="GC shield test")
    output = WorkflowOutput(workflow="mock_wf", answer="GC answer")

    await runner._adispatch_listeners("completed", request, output=output)

    # Wait for the background task to start
    await task_started.wait()

    # Verify task is currently in both GC shielding sets
    assert len(runner._background_tasks) == 1
    active_task = next(iter(runner._background_tasks))
    assert active_task in _background_tasks
    assert not active_task.done()

    # Release the task and let it complete
    task_finish.set()
    await asyncio.sleep(0.01)

    assert active_task.done()
    assert len(runner._background_tasks) == 0
    assert active_task not in _background_tasks


def test_dispatch_listeners_sync_delegation() -> None:
    """Verify synchronous _dispatch_listeners executes safely via run_async_safely."""
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[spy])

    request = WorkflowInput(question="Sync dispatch test")
    output = WorkflowOutput(workflow="mock_wf", answer="Sync answer")

    # Call synchronously without an active event loop in this thread
    runner._dispatch_listeners("completed", request, output=output)

    assert len(spy.completed_calls) == 1
    assert spy.completed_calls[0] == (request, output)


@pytest.mark.asyncio
async def test_adispatch_listeners_guards_missing_output() -> None:
    """Verify completed event without output logs warning and skips listener invocation."""
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[spy])
    request = WorkflowInput(question="Missing output test")

    await runner._adispatch_listeners("completed", request, output=None)
    assert len(spy.completed_calls) == 0


@pytest.mark.asyncio
async def test_adispatch_listeners_guards_missing_error() -> None:
    """Verify error event without error logs warning and skips listener invocation."""
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[spy])
    request = WorkflowInput(question="Missing error test")

    await runner._adispatch_listeners("error", request, error=None)
    assert len(spy.error_calls) == 0


@pytest.mark.asyncio
async def test_adispatch_listeners_guards_unknown_event() -> None:
    """Verify unsupported event type logs warning and skips listener invocation."""
    spy = SpyListener()
    runner = WorkflowRunner(listeners=[spy])
    request = WorkflowInput(question="Unknown event test")
    output = WorkflowOutput(workflow="mock_wf", answer="Some answer")

    await runner._adispatch_listeners("unknown_lifecycle_event", request, output=output)
    assert len(spy.completed_calls) == 0
    assert len(spy.error_calls) == 0
    assert len(spy.cancelled_calls) == 0
    assert len(spy.background_calls) == 0
