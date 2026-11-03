# ADR 0019: Decouple Workflow Execution from Transport Wire Protocol and Lifecycle Side-Effects

## Status
Accepted (Extends ADR 0004, ADR 0007, ADR 0010, ADR 0012, and ADR 0016)

## Context
Following ADR-0010 (Workflow Runner Execution Pipeline) and ADR-0012 (Internalize Runtime Hooks), `WorkflowRunner` served as the single entry point for workflow invocation. While this eliminated route-level coordination shims, `WorkflowRunner` grew into a 1,140-line catch-all module burdened with conflicting responsibilities:
1. **Transport Wire Formatting in Execution Core**: `WorkflowRunner.stream()` directly invoked `format_sse()`, generating HTTP Server-Sent Event text strings (`"event: token\ndata: ...\n\n"`). This tightly coupled graph execution with HTTP/1.1 SSE wire semantics and forced domain tests to parse text with regular expressions.
2. **Entangled Post-Execution Side-Effects**: `WorkflowRunner` directly executed SQLite turn persistence (`_apersist_turn`), managed background task collections (`_background_tasks`), and inspected event loops. These side-effects could not be isolated during headless evaluation runs or offline matrix benchmarking.
3. **Implicit Memory Fallback**: When no memory store was injected, `WorkflowRunner` defaulted to `get_conversation_memory()`, opening the production SQLite database connection even during unit testing and stateless benchmark executions.
4. **Scattered Exception Handling**: Uncaught streaming exceptions severed client sockets abruptly without emitting an application-level failure event or executing cleanup listeners.

To achieve strict modularity, swappable workflows, zero-transport execution, and headless benchmarking, the execution core must be decoupled from transport wire formatting and post-execution side-effects.

---

## Decisions

### 1. Strongly-Typed Domain Event Model (`app/rag/workflows/events.py`)
Define a transport-agnostic discriminated union of workflow execution events:
- `StepTransitionEvent(step: str)`: Emitted when graph node transitions occur (e.g. `retrieve`, `grade_evidence`).
- `TokenDeltaEvent(delta: str)`: Emitted for whitelisted LLM response token chunks.
- `ExecutionCompletedEvent(output: WorkflowOutput)`: Emitted upon successful execution completion, carrying the canonical `WorkflowOutput` domain model (answer, trace, citations, source_used, trace_id, rewritten_query, variant_id).
- `ExecutionFailedEvent(error: str, code: str)`: Emitted when an uncaught exception occurs during execution, terminating the stream cleanly without an abrupt socket tear-down.

`WorkflowRunner.stream(request)` yields `AsyncGenerator[WorkflowExecutionEvent, None]`, completely ignorant of HTTP, SSE frames, or text serialization.

### 2. Transport Wire Seam (`app/api/streaming.py`)
Isolate all SSE framing, ping keepalives, and wire serialization in `SseStreamAdapter` residing in the HTTP presentation layer (`app/api/streaming.py`):
```python
class SseStreamAdapter:
    @classmethod
    async def format(cls, events: AsyncGenerator[WorkflowExecutionEvent, None]) -> AsyncGenerator[str, None]:
        async for event in events:
            if isinstance(event, StepTransitionEvent):
                yield format_sse("step", {"step": event.step})
            elif isinstance(event, TokenDeltaEvent):
                yield format_sse("token", {"delta": event.delta})
            elif isinstance(event, ExecutionCompletedEvent):
                yield format_sse("metadata", event.output.model_dump())
            elif isinstance(event, ExecutionFailedEvent):
                yield format_sse("error", {"error": event.error, "code": event.code})
```
**Strict Dependency Direction**: `app/rag/workflows/runner.py` has **zero imports** from `app/api/` and zero awareness of SSE or HTTP transport.

### 3. Pluggable Execution Lifecycle Listeners (`app/rag/workflows/listeners.py`)
Decouple post-execution side-effects behind an event-driven `ExecutionLifecycleListener` protocol:
```python
@runtime_checkable
class ExecutionLifecycleListener(Protocol):
    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Invoked when execution succeeds. Awaited before terminal event emission."""
        ...

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Invoked concurrently in a detached background task without blocking response."""
        ...

    async def on_error(self, request: WorkflowInput, error: Exception) -> None:
        """Invoked when execution fails before terminal event emission."""
        ...

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        """Invoked when execution is cancelled by client disconnect."""
        ...
```
Concrete adapters:
- `MemoryPersistenceListener`: Implements `on_completed` (awaited for immediate turn persistence to prevent race conditions on rapid sequential turns) and discards incomplete turns on `on_cancelled`.
- `OnlineEvaluationListener`: Implements `on_background_task` (scheduled in loop or via FastAPI `BackgroundTasks`) to evaluate answers without adding latency to user responses.

### 4. Explicit Memory Pluggability & Headless Mode
Remove implicit fallback to the global SQLite database inside `WorkflowRunner`. Passing `memory=None` or `NoOpMemoryStore` completely bypasses:
1. Pre-execution conversational history hydration queries.
2. Post-execution turn persistence attempts.
Headless evaluation scripts (`run_experiment.py`) and matrix benchmarks can run with `memory=None` and an empty listener list with zero disk I/O.

### 5. Test Modernization Strategy
- Transport tests (`tests/test_chat_stream.py`) assert against wire frames produced by `SseStreamAdapter.format(...)`.
- Domain execution tests (`tests/rag/test_workflow_execution.py`) assert directly against strongly-typed `WorkflowExecutionEvent` instances without regex text parsing.

---

## Consequences

### Positive
- **Locality**: Transport-level wire encoding is isolated in `app/api/streaming.py`; graph execution is isolated in `app/rag/workflows/runner.py`.
- **Leverage**: Any consumer (FastAPI SSE, WebSockets, CLI benchmarks, offline test doubles) can consume workflow execution events directly without parsing text strings.
- **Testability**: Tests verify structured event objects rather than regex pattern-matching on SSE strings.
- **Modularity**: Background evaluation and turn persistence can be attached, detached, or swapped without touching the execution engine.

### Strict Non-Goals
- Modifying LangGraph node topologies or classifier seams.
- Introducing external message brokers (Kafka/RabbitMQ) for in-process lifecycle events.
