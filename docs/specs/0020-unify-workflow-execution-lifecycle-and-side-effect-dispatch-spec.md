# Technical Specification: Unify Workflow Execution Lifecycle and Side-Effect Dispatch

- **ADR Reference:** `docs/adr/0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch.md`
- **Target Subsystems:** `app/rag/workflows/`, `app/api/`, `tests/rag/`, `tests/api/`, `tests/test_online_evaluation.py`, `tests/test_workflow_runner.py`
- **Specification Document:** `docs/spec.md` & `docs/specs/0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch-spec.md`
- **Status:** Draft / Ready for Implementation

---

## Problem Statement

Following the introduction of the `ExecutionLifecycleListener` protocol in ADR-0019, `WorkflowRunner.stream()` successfully delegated post-execution side-effects (conversation turn persistence and background judge evaluation) to pluggable listener adapters. However, `WorkflowRunner` was left in an asymmetric and contradictory architectural state across its execution modes:

1. **Split Execution Pathways & Bypass Anti-Pattern**: In `execute()` and `aexecute()`, `WorkflowRunner` bypassed the `ExecutionLifecycleListener` seam completely. Instead, it invoked duplicate internal private persistence methods (`_apersist_turn`, `_persist_turn_sync`) and private evaluation methods (`_safe_evaluate`), creating two parallel side-effect engines within the same class.
2. **Mutable Invalidation Setters**: `WorkflowRunner` retained mutable getters and setters (`memory`, `evaluator`, `eval_task_fn`) that dynamically set `self._listeners = None` to force cache invalidation. This hidden mutability encouraged callers and test fixtures to mutate runner singletons in-place rather than injecting clean dependencies at initialization.
3. **Transport Leakage in Execution Interfaces**: `execute()`, `execute_with_context()`, and `aexecute()` accepted an optional Starlette/FastAPI `background_tasks: BackgroundTasks | None = None` parameter. Passing transport-specific framework objects across domain seams violated the clean separation between `app/api/` and `app/rag/`.
4. **Scattered and Inconsistent Failure Handling**: While streaming execution caught cancellations and notified listeners via `on_cancelled`, unhandled errors during synchronous and asynchronous execution did not invoke `listener.on_error()`. This prevented centralized observability, audit logging, or error telemetry listeners from detecting graph execution failures.
5. **Coupled Read-Path Hydration and Write-Path Persistence**: In `WorkflowRunner`, the `ConversationMemoryAdapter` dependency was treated ambiguously as both an input reader (pre-execution hydration of message history) and an output writer (post-execution SQLite append), blurring the boundary between input resolution and observer side-effects.

---

## Solution

Unify all workflow execution modes (`execute`, `aexecute`, and `stream`) on the `ExecutionLifecycleListener` protocol, prune all internal bypass methods and mutable setters from `WorkflowRunner`, remove transport leakage, and strictly isolate read hydration from write persistence:

1. **Unified Listener Dispatching**:
   Implement internal dispatch helpers `_adispatch_listeners` and `_dispatch_listeners` (via `run_async_safely`) in `WorkflowRunner` that invoke lifecycle hooks uniformly across all execution modes:
   - `on_completed`: Awaited sequentially across registered listeners. Fail-open isolation ensures that transient persistence or evaluation errors log warnings without aborting user response delivery.
   - `on_background_task`: Scheduled concurrently via `asyncio.create_task` with GC-shielding tracking sets.
   - `on_error`: Awaited across registered listeners when graph execution fails before re-raising or returning fallback output.
   - `on_cancelled`: Awaited across registered listeners when streaming connections abort.
2. **Prune Bypass Methods and Mutable Setters**:
   Delete `_apersist_turn`, `_persist_turn_sync`, and `_safe_evaluate` from `WorkflowRunner`. Remove mutable property setters for `memory`, `evaluator`, `eval_task_fn`, and `listeners`. Treat `listeners: Sequence[ExecutionLifecycleListener]` as the definitive side-effect seam configured at initialization.
3. **Read-Path Hydration Boundary**:
   Keep `memory: ConversationMemoryAdapter | None = None` in `WorkflowRunner` strictly as an input read-path dependency for hydrating past turns in `_prepare_execution`. Write-path persistence is delegated exclusively to `MemoryPersistenceListener`.
4. **Pre-Notification EvalContext Normalization**:
   Guarantee that `WorkflowRunner` extracts and binds `output.retrieved_contexts` to canonical `WorkflowOutput` prior to notifying listeners, ensuring `OnlineEvaluationListener` receives normalized context chunks without inspecting graph internals.
5. **Pure Python Transport-Agnostic Interface**:
   Remove `background_tasks: BackgroundTasks | None` from `WorkflowRunner` execution methods. Update `POST /api/chat` to a pure, declarative signature without `BackgroundTasks`.

---

## User Stories

1. As a platform developer, I want `WorkflowRunner.execute()`, `aexecute()`, and `stream()` to dispatch side-effects through the exact same `ExecutionLifecycleListener` seam, so that persistence and evaluation behavior are identical across all execution modes.
2. As a test author, I want to instantiate `WorkflowRunner(listeners=[])`, so that I can run completely isolated tests with zero database writes, zero background tasks, and zero filesystem I/O.
3. As a test author, I want to inject a custom `ExecutionLifecycleListener` into `WorkflowRunner`, so that I can assert on lifecycle events (`on_completed`, `on_error`) without mocking internal private methods.
4. As an API client using `POST /api/chat`, I want my conversational turns to be persisted atomically to SQLite via `MemoryPersistenceListener`, so that subsequent conversational turns have access to prior message context.
5. As an end user receiving an answer, I want transient database or evaluation failures to be logged as warnings without failing my query, so that I always receive the generated answer (fail-open invariant).
6. As an operations engineer monitoring production, I want unhandled graph exceptions in `execute()` and `aexecute()` to trigger `listener.on_error(request, exc)`, so that audit and telemetry listeners record failure details reliably.
7. As an architect enforcing domain decoupling, I want `WorkflowRunner` execution signatures to be completely free of Starlette/FastAPI `BackgroundTasks`, so that core workflows can execute inside Celery workers, CLI tools, or batch scripts without web framework dependencies.
8. As a backend engineer maintaining HTTP routes, I want `POST /api/chat` to have a clean signature (`def chat(payload: ChatRequest) -> ChatResponse`), so that routing code remains uncluttered by unused framework parameters.
9. As a developer writing multi-turn conversations, I want `WorkflowRunner` to hydrate past conversation messages using `self.memory` before graph execution begins, so that retrieval condensation nodes have access to previous turns.
10. As a developer adding an online evaluation metric, I want `WorkflowRunner` to populate `output.retrieved_contexts` before calling `OnlineEvaluationListener`, so that faithfulness and answer relevancy metrics have direct access to retrieved document chunks.
11. As a developer configuring application startup, I want `get_workflow_runner(memory=..., evaluator=...)` to automatically configure default `[MemoryPersistenceListener, OnlineEvaluationListener]` instances when `listeners` is omitted, so that production DI wiring remains concise and backward-compatible.
12. As a developer building custom harnesses, I want `WorkflowRunner(listeners=[my_listener])` to prioritize the explicit `listeners` list over defaults, so that I have complete control over side-effect execution.
13. As a maintainer inspecting `WorkflowRunner`, I want `_apersist_turn`, `_persist_turn_sync`, and `_safe_evaluate` to be completely removed, so that there is no possibility of accidental bypass in future code changes.
14. As a developer debugging streaming disconnects, I want `on_cancelled` to be invoked cleanly across listeners when a client disconnects, ensuring incomplete assistant answers are never persisted.
15. As a developer running synchronous benchmarks, I want background evaluation tasks triggered by synchronous `execute()` to run safely on the persistent background concurrency loop via `run_async_safely`, so that event loop collisions never occur.

---

## Implementation Decisions

### 1. Unified Dispatch Seam in `WorkflowRunner`

Add private dispatch helpers to `WorkflowRunner` in `app/rag/workflows/runner.py`:

```python
async def _adispatch_listeners(
    self,
    event: str,
    request: WorkflowInput,
    output: WorkflowOutput | None = None,
    error: Exception | None = None,
) -> None:
    for listener in self.listeners:
        try:
            if event == "completed" and output is not None:
                await listener.on_completed(request, output)
            elif event == "error" and error is not None:
                await listener.on_error(request, error)
            elif event == "cancelled":
                await listener.on_cancelled(request, output)
        except Exception as exc:
            logger.warning(
                f"listener_{event}_failed",
                listener=listener.__class__.__name__,
                error=str(exc),
            )

    if event == "completed" and output is not None:
        for listener in self.listeners:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(listener.on_background_task(request, output))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            except Exception as exc:
                logger.warning(
                    "listener_background_task_scheduling_failed",
                    listener=listener.__class__.__name__,
                    error=str(exc),
                )
```

For synchronous execution (`execute()`):
```python
def _dispatch_listeners(
    self,
    event: str,
    request: WorkflowInput,
    output: WorkflowOutput | None = None,
    error: Exception | None = None,
) -> None:
    run_async_safely(self._adispatch_listeners, event, request, output, error)
```

### 2. Prune Bypass Logic and Mutable Properties

In `app/rag/workflows/runner.py`:
- Delete `_apersist_turn`, `_persist_turn_sync`, and `_safe_evaluate`.
- Delete property setters for `memory`, `evaluator`, `eval_task_fn`, and `listeners`.
- In `_finalize_execution`: Remove inline calls to `self._safe_evaluate` and `self._persist_turn_sync`. Replace with `self._dispatch_listeners("completed", request, domain_output)`.
- In `_afinalize_execution`: Replace inline calls with `await self._adispatch_listeners("completed", request, domain_output)`.
- In `stream()`: Replace inline listener loop iterations with `await self._adispatch_listeners("completed", request, domain_output)`.
- On execution error: In `_execute_with_state`, `_aexecute_with_state`, and `_stream_generator`, dispatch `error` event: `_dispatch_listeners("error", request, error=exc)` or `await self._adispatch_listeners("error", request, error=exc)`.

### 3. Factory and Constructor Defaults

In `WorkflowRunner.__init__`:
```python
def __init__(
    self,
    registry: WorkflowRegistry | None = None,
    *,
    memory: ConversationMemoryAdapter | None = None,
    evaluator: Evaluator | None = None,
    listeners: Sequence[ExecutionLifecycleListener] | None = None,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
) -> None:
    self.registry = registry if registry is not None else get_workflow_registry()
    self._memory = memory
    self._callback_factory = callback_factory
    self._trace_extractor = trace_extractor
    self._telemetry_flusher = telemetry_flusher
    self._background_tasks: set[asyncio.Task[Any]] = set()

    if listeners is not None:
        self._listeners = list(listeners)
    else:
        defaults: list[ExecutionLifecycleListener] = []
        if memory is not None and not isinstance(memory, NoOpMemoryStore):
            defaults.append(MemoryPersistenceListener(memory=memory))
        if evaluator is not None or eval_task_fn is not None:
            defaults.append(
                OnlineEvaluationListener(
                    evaluator=evaluator or (get_evaluator() if evaluator is None and eval_task_fn is None else None),
                    eval_task_fn=eval_task_fn,
                )
            )
        self._listeners = defaults
```

Provide read-only properties:
- `runner.listeners -> list[ExecutionLifecycleListener]`
- `runner.memory -> ConversationMemoryAdapter | None`

### 4. Remove `BackgroundTasks` from Runner & HTTP Routes

- In `WorkflowRunner.execute`, `execute_with_context`, and `aexecute`: Remove `background_tasks: BackgroundTasks | None = None` parameter from signatures and call sites.
- In `app/api/routes.py`:
  ```python
  @router.post("/chat", response_model=ChatResponse)
  def chat(payload: ChatRequest):
      wf_input = payload.to_workflow_input()
      wf_output = get_workflow_runner().execute(wf_input)
      return ChatResponse.from_workflow_output(wf_output)
  ```

---

## Testing Decisions

### What Makes a Good Test

Tests must verify external behavioral contracts across the public execution interface (`execute`, `aexecute`, `stream`) rather than testing private internal state:
1. **Unified Listener Notification**: Assert that passing a custom `spy_listener` records identical `on_completed` invocations across `execute()`, `aexecute()`, and `stream()`.
2. **Error Notification**: Assert that when a workflow graph raises an unhandled exception, `on_error` is invoked with the expected exception instance across sync, async, and streaming routes.
3. **Fail-Open Isolation**: Assert that when a listener raises an unhandled error inside `on_completed` or `on_background_task`, `WorkflowRunner` logs a warning and returns the user's `WorkflowOutput` cleanly without raising an exception.
4. **Zero-Side-Effect Isolation**: Assert that `WorkflowRunner(listeners=[])` performs zero SQLite turn appends and zero judge evaluations during execution.
5. **Route Cleanliness**: Assert that `POST /api/chat` returns 200 and valid responses without requiring `BackgroundTasks`.

### Modules to Test

- `tests/rag/test_workflow_execution.py`: Expand listener dispatch tests across `execute`, `aexecute`, and `stream`.
- `tests/rag/test_workflow_runner.py`: Verify constructor defaults, read hydration, error dispatching, and removal of private bypass methods.
- `tests/rag/test_workflow_runner_memory.py`: Verify that turn persistence occurs exclusively via `MemoryPersistenceListener` on completed executions.
- `tests/api/test_routes_chat_memory.py`: Verify end-to-end chat memory behavior with the updated `/api/chat` signature.

### Prior Art

- `tests/rag/test_workflow_execution.py`: Provides existing spy and failing listener patterns from ADR-0019.
- `tests/rag/test_workflow_runner.py`: Provides canonical mock workflow definition harnesses.

---

## Out of Scope

- Changing the `ExecutionLifecycleListener` protocol method signatures (`on_completed`, `on_background_task`, `on_error`, `on_cancelled`).
- Modifying the SSE wire formatting logic in `SseStreamAdapter` (addressed separately in Candidate #2).
- Modifying the vector store indexing pipeline in `DocumentIndexer` (addressed separately in Candidate #3).

---

## Further Notes

- This specification completes the architectural journey of ADR-0010, ADR-0012, and ADR-0019, turning `WorkflowRunner` into a completely cohesive, zero-bypass, deep execution module.
- All changes maintain 100% backward compatibility with existing frontend clients and external workflows.
