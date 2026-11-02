# Technical Specification: Decouple Workflow Execution from Transport Wire Protocol and Lifecycle Side-Effects

- **ADR Reference:** `docs/adr/0019-decouple-workflow-execution-from-transport-wire-protocol.md`
- **Target Subsystems:** `app/rag/workflows/`, `app/api/`, `tests/rag/`, `tests/test_chat_stream.py`
- **Specification Document:** `docs/spec.md` & `docs/specs/0019-decouple-workflow-execution-from-transport-wire-protocol-spec.md`
- **Status:** Draft / Ready for Implementation

---

## Problem Statement

As the Enterprise Agentic RAG system expanded to support multiple execution backends, dynamic execution profiles, in-place turn rewind, and offline matrix benchmarking, `WorkflowRunner` in `app/rag/workflows/runner.py` became a 1,140-line catch-all module burdened with conflicting domain, transport, and infrastructure concerns:

1. **Coupled Transport Serialization**: The streaming entrypoint `WorkflowRunner.stream()` directly imports and invokes `format_sse()`, emitting raw HTTP Server-Sent Event strings (`"event: token\ndata: ...\n\n"`). This tightly couples the graph execution runtime with HTTP/1.1 transport semantics, preventing reuse across non-HTTP callers (such as CLI tools, headless benchmarks, or future WebSocket interfaces) and forcing test suites to use regex parsing on text strings to verify execution behavior.
2. **Entangled Post-Execution Side-Effects**: `WorkflowRunner` directly interacts with SQLite turn persistence transactions (`_apersist_turn`), manages background task collections (`_background_tasks`), and manipulates asyncio event loops. These side-effects cannot be cleanly decoupled during headless evaluation runs or matrix benchmarking, causing unwanted SQLite I/O or necessitating complex mock patching.
3. **Implicit Memory Fallback**: When instantiated without an explicit `memory` store, `WorkflowRunner` defaults to `get_conversation_memory()`, opening the global `data/conversations.db` database connection even during unit testing and stateless benchmark executions.
4. **Fragile Error Handling in Streaming**: Uncaught exceptions during graph execution cause abrupt socket disconnections without emitting application-level failure frames or invoking cleanup hooks.
5. **Violated Architectural Dependency Rules**: The execution engine should be purely in-process and transport-agnostic, with strict unidirectional dependencies flowing from `app/api/` into `app/rag/`, and never the reverse.

---

## Solution

Decouple `WorkflowRunner` into a pure, transport-agnostic execution engine that yields strongly-typed domain events, delegates post-execution side-effects to an event-driven lifecycle listener protocol, and moves all SSE wire serialization to a presentation adapter in `app/api/`:

1. **Domain Event Stream (`app/rag/workflows/events.py`)**:
   Define a discriminated union `WorkflowExecutionEvent` (`StepTransitionEvent`, `TokenDeltaEvent`, `ExecutionCompletedEvent`, `ExecutionFailedEvent`). `WorkflowRunner.stream()` yields this typed generator, completely ignorant of HTTP or SSE.
2. **Pluggable Lifecycle Listeners (`app/rag/workflows/listeners.py`)**:
   Establish an `ExecutionLifecycleListener` protocol with hooks for `on_completed` (awaited for immediate turn persistence), `on_background_task` (detached background tasks for online evaluation), `on_error` (failure handling), and `on_cancelled` (clean resource flushing on client disconnect). Provide a `BaseExecutionLifecycleListener` with default no-op methods and concrete adapters `MemoryPersistenceListener` and `OnlineEvaluationListener`.
3. **Transport Wire Adapter (`app/api/streaming.py`)**:
   Implement `SseStreamAdapter` in `app/api/` that translates `AsyncGenerator[WorkflowExecutionEvent, None]` into SSE text frames formatted identically to current client expectations (`step`, `token`, `metadata`, and `error`).
4. **Explicit Memory Pluggability**:
   Require memory to be explicit. If `memory is None` or `isinstance(memory, NoOpMemoryStore)`, completely bypass pre-execution history hydration and post-execution turn persistence, executing with zero SQLite or disk overhead.
5. **Strict Unidirectional Seam**:
   `app/rag/workflows/runner.py` retains **zero imports** from `app/api/` and zero awareness of HTTP or SSE wire protocols.

---

## User Stories

1. As a platform developer, I want `WorkflowRunner.stream()` to yield strongly-typed `WorkflowExecutionEvent` objects, so that I can consume streaming graph execution in non-HTTP environments (CLI benchmarks, WebSockets, offline test harnesses) without parsing SSE text strings.
2. As a test author, I want to assert on `StepTransitionEvent(step="grade_evidence")` and `TokenDeltaEvent(delta="...")` directly, so that my unit tests do not break when whitespace or JSON formatting changes in the transport layer.
3. As a developer running offline matrix benchmarks, I want to instantiate `WorkflowRunner` with `memory=None` and empty listeners, so that benchmark iterations execute with zero database I/O, no background judge tasks, and maximum speed.
4. As an API client receiving streaming responses, I want `SseStreamAdapter` to serialize `ExecutionCompletedEvent` into a `metadata` event frame containing `trace_id`, `session_id`, `workflow`, `source_used`, `citations`, `trace`, `rewritten_query`, and `variant_id`, so that the frontend UI continues to render citations and accordions without modification.
5. As an API client encountering a runtime graph error during streaming, I want `SseStreamAdapter` to emit an `error` frame before the connection closes, so that my UI displays an informative error toast instead of an unexplained broken stream.
6. As a conversational user submitting rapid follow-up queries, I want `MemoryPersistenceListener.on_completed` to be awaited before `ExecutionCompletedEvent` is emitted, so that prior conversation turns are guaranteed to be in SQLite before my next query begins.
7. As an end user streaming a long response who closes the browser tab, I want `WorkflowRunner` to invoke `on_cancelled` on listeners, so that incomplete half-sentence assistant responses are discarded and never persisted to my session history.
8. As an operations engineer monitoring production performance, I want `OnlineEvaluationListener` to run as a detached background task, so that judge model evaluations never add latency to the user's streaming response.
9. As an architect enforcing domain cleanliness, I want `app/rag/workflows/runner.py` to contain zero references to `format_sse`, `text/event-stream`, or `app/api/`, so that the execution core remains strictly decoupled from presentation logic.
10. As a developer adding an audit logging plugin, I want to register a custom `ExecutionLifecycleListener`, so that I can observe completed workflow outputs without modifying the runner's execution logic.
11. As a developer configuring execution parameters, I want `WorkflowRunner.execute()` and `WorkflowRunner.aexecute()` to trigger lifecycle listeners identically to streaming execution, so that turn persistence and online evaluation behave consistently across sync, async, and streaming routes.
12. As a developer testing edge cases, I want `BaseExecutionLifecycleListener` to provide default no-op implementations for all hooks, so that I only need to implement the specific lifecycle methods relevant to my adapter.
13. As a maintainer inspecting the codebase, I want `app/api/routes.py` to clearly route `POST /api/chat/stream` through `SseStreamAdapter.format(runner.stream(request))`, so that the transport seam is visible and readable.
14. As a test author writing integration tests in `tests/test_chat_stream.py`, I want `SseStreamAdapter` to be verified for correct SSE formatting (event names, JSON escaping, multi-line data), so that wire regressions are caught at the presentation seam.
15. As a developer inspecting dependency boundaries, I want `app/rag/workflows/__init__.py` to export the public events (`WorkflowExecutionEvent`, etc.) and listeners (`ExecutionLifecycleListener`, etc.), so that external packages import canonical domain symbols cleanly.

---

## Implementation Decisions

### 1. Domain Event Subsystem (`app/rag/workflows/events.py`)

Create `app/rag/workflows/events.py` defining the discriminated domain event union:

```python
from typing import Literal, Union
from pydantic import BaseModel, Field
from app.api.schemas import WorkflowOutput


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


WorkflowExecutionEvent = Union[
    StepTransitionEvent,
    TokenDeltaEvent,
    ExecutionCompletedEvent,
    ExecutionFailedEvent,
]
```

### 2. Execution Lifecycle Listener Protocol (`app/rag/workflows/listeners.py`)

Create `app/rag/workflows/listeners.py` defining the lifecycle listener interface and concrete implementations:

```python
from typing import Any, Protocol, runtime_checkable
from app.api.schemas import WorkflowInput, WorkflowOutput


@runtime_checkable
class ExecutionLifecycleListener(Protocol):
    """Protocol for receiving execution lifecycle notifications and executing side-effects."""

    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Awaited immediately upon successful execution before terminal event emission."""
        ...

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Executed concurrently in a detached background task without delaying response."""
        ...

    async def on_error(self, request: WorkflowInput, error: Exception) -> None:
        """Invoked when execution fails with an exception."""
        ...

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        """Invoked when execution is cancelled by client disconnect."""
        ...


class BaseExecutionLifecycleListener:
    """Default no-op implementation of ExecutionLifecycleListener."""

    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        pass

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        pass

    async def on_error(self, request: WorkflowInput, error: Exception) -> None:
        pass

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        pass
```

#### Concrete Adapters in `app/rag/workflows/listeners.py`:
- **`MemoryPersistenceListener`**:
  - Implements `on_completed`: Awaits `memory.aappend_turn(...)` when `request.session_id` and `output.answer` are present.
  - Implements `on_cancelled`: Discards partial state; does NOT persist incomplete turns to SQLite.
- **`OnlineEvaluationListener`**:
  - Implements `on_background_task`: Schedules `eval_task_fn(...)` safely, catching and logging any judge evaluation exceptions without affecting the caller.

### 3. Transport Wire Adapter (`app/api/streaming.py`)

Create `app/api/streaming.py` isolating all Server-Sent Events serialization logic:

```python
from typing import AsyncGenerator
from app.rag.workflows.events import (
    ExecutionCompletedEvent,
    ExecutionFailedEvent,
    StepTransitionEvent,
    TokenDeltaEvent,
    WorkflowExecutionEvent,
)
from app.rag.workflows.runner import format_sse  # Wire helper moved or imported


class SseStreamAdapter:
    """Presentation adapter translating domain WorkflowExecutionEvent streams into HTTP SSE text frames."""

    @classmethod
    async def format(
        cls,
        events: AsyncGenerator[WorkflowExecutionEvent, None],
    ) -> AsyncGenerator[str, None]:
        async for event in events:
            if isinstance(event, StepTransitionEvent):
                yield format_sse("step", {"step": event.step})
            elif isinstance(event, TokenDeltaEvent):
                yield format_sse("token", {"delta": event.delta})
            elif isinstance(event, ExecutionCompletedEvent):
                output = event.output
                metadata_payload = {
                    "trace_id": output.trace_id,
                    "session_id": output.session_id,
                    "workflow": output.workflow,
                    "source_used": output.source_used,
                    "citations": [c.model_dump() if hasattr(c, "model_dump") else c for c in output.citations],
                    "trace": output.trace,
                    "rewritten_query": output.rewritten_query,
                    "variant_id": output.variant_id,
                }
                yield format_sse("metadata", metadata_payload)
            elif isinstance(event, ExecutionFailedEvent):
                yield format_sse("error", {"error": event.error, "code": event.code})
```

### 4. Refactoring `WorkflowRunner` (`app/rag/workflows/runner.py`)

1. **Remove Wire Coupling**:
   - Delete all `yield self.format_sse(...)` calls from `stream()`.
   - Instead, `yield StepTransitionEvent(step=node_name)`, `yield TokenDeltaEvent(delta=delta_content)`, and `yield ExecutionCompletedEvent(output=domain_output)`.
   - Wrap graph iteration in `try...except Exception as exc:` to catch uncaught stream errors, invoke `listener.on_error(...)`, and `yield ExecutionFailedEvent(error=str(exc))`.
   - In `except (asyncio.CancelledError, GeneratorExit):`, invoke `listener.on_cancelled(...)` and cleanly flush telemetry.
2. **Pluggable Listeners**:
   - `WorkflowRunner.__init__` accepts `listeners: list[ExecutionLifecycleListener] | None = None`.
   - If `listeners is None`, configure default listeners:
     - `MemoryPersistenceListener(memory=self._memory)` (only if `self._memory` is active and not `NoOpMemoryStore`).
     - `OnlineEvaluationListener(eval_task_fn=self.eval_task_fn or self._safe_evaluate)`.
3. **Explicit Memory**:
   - `_memory: ConversationMemoryAdapter | None = None`.
   - If `self._memory is None`, do NOT fall back to `get_conversation_memory()`. Memory hydration and persistence are completely skipped.
4. **Lifecycle Hooks in Execution**:
   - In `_finalize_execution` (sync/async) and `stream()`, trigger:
     - Await all `listener.on_completed(request, domain_output)` for immediate listeners.
     - Dispatch `listener.on_background_task(request, domain_output)` for background tasks.
5. **Zero API Imports**:
   - Verify `runner.py` contains zero imports from `app/api/`.

### 5. HTTP Routes Simplification (`app/api/routes.py`)

Update `POST /api/chat/stream`:
```python
@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    wf_input = request.to_workflow_input()
    event_stream = get_workflow_runner().stream(wf_input)
    return StreamingResponse(
        SseStreamAdapter.format(event_stream),
        media_type="text/event-stream; charset=utf-8",
    )
```

---

## Testing Decisions

### What Makes a Good Test
- Tests must assert external behavior across documented seams, never inspecting private runner variables or internal call counts.
- Domain tests verify structured `WorkflowExecutionEvent` types and properties.
- Wire tests verify SSE framing semantics and HTTP streaming compatibility.
- Offline tests verify that passing `memory=None` performs zero disk or SQLite operations.

### Modules to Test
1. **`tests/rag/test_workflow_events.py`**:
   - Unit test serialization and validation of `WorkflowExecutionEvent` models.
2. **`tests/rag/test_workflow_listeners.py`**:
   - Verify `MemoryPersistenceListener` awaits turn persistence and discards turns on cancellation.
   - Verify `OnlineEvaluationListener` safely schedules detached evaluation.
3. **`tests/rag/test_workflow_execution.py`**:
   - Verify `WorkflowRunner.stream()` yields typed events in order: `StepTransitionEvent` -> `TokenDeltaEvent`* -> `ExecutionCompletedEvent`.
   - Verify error during streaming yields `ExecutionFailedEvent`.
   - Verify client disconnect triggers `on_cancelled`.
   - Verify `memory=None` executes cleanly with zero memory interactions.
4. **`tests/api/test_sse_stream_adapter.py`**:
   - Unit test `SseStreamAdapter.format()` converting typed events into valid SSE text frames.
5. **`tests/test_chat_stream.py`**:
   - End-to-end HTTP streaming regression tests ensuring the existing client/UI contract remains 100% backward-compatible.

### Prior Art
- `tests/rag/test_workflow_runner.py`: Existing tests for parameter precedence and graph compilation.
- `tests/test_chat_stream.py`: Existing SSE streaming route tests.
- `tests/rag/test_conversation_memory.py`: Existing memory adapter seam testing patterns.

---

## Out of Scope

- Modifying LangGraph node definitions (`retrieve_kb`, `grade_evidence`, etc.) in `app/rag/engine.py`.
- Changing the schema of SQLite `conversations.db` or `ConversationTurn`.
- Introducing external distributed task workers (Celery, RQ, RabbitMQ).
- Modifying frontend DOM construction logic in `static/js/app.js` (client consumes the identical SSE frames).

---

## Further Notes

- By decoupling wire formatting into `SseStreamAdapter`, the codebase is now prepared for future presentation adapters (such as WebSocket streaming or gRPC) without touching the RAG execution engine.
- This refactoring reduces `WorkflowRunner` by ~300 lines of wire serialization and ad-hoc background task code, significantly improving maintainability and test execution speed.
