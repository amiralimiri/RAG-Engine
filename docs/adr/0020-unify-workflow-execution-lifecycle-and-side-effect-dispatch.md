# ADR 0020: Unify Workflow Execution Lifecycle and Side-Effect Dispatch

## Status
Accepted (Extends ADR 0010, ADR 0012, ADR 0016, and ADR 0019)

## Context
ADR-0019 introduced the `ExecutionLifecycleListener` protocol (`MemoryPersistenceListener`, `OnlineEvaluationListener`) and the domain event stream for `WorkflowRunner.stream()`. However, this left an architectural asymmetry across the runner's execution entry points:

1. **Split Execution Paths**: `stream()` notified `ExecutionLifecycleListener` adapters for turn persistence and background evaluation. In contrast, `execute()` and `aexecute()` bypassed listeners entirely, directly invoking legacy private persistence (`_apersist_turn`, `_persist_turn_sync`) and evaluation helpers (`_safe_evaluate`).
2. **Mutable Invalidation Setters**: `WorkflowRunner` retained mutable property getters/setters (`memory`, `evaluator`, `eval_task_fn`) that dynamically cleared or rebuilt the cached listener list (`self._listeners`), creating hidden state mutations.
3. **Transport Leakage in Execution Seam**: `execute()` and `aexecute()` accepted Starlette/FastAPI `background_tasks: BackgroundTasks | None`, leaking transport concerns into the core workflow execution interface.
4. **Scattered Error Notification**: When execution failed, `stream()` notified `listener.on_cancelled` on client abort, but exceptions during `execute()` and `aexecute()` did not notify `listener.on_error`, preventing centralized failure telemetry.

To establish strict modularity, `WorkflowRunner` must treat `ExecutionLifecycleListener` as the single side-effect seam across all execution modes.

---

## Decisions

### 1. Unified Dispatch Seam (`_adispatch_listeners` and `_dispatch_listeners`)
Consolidate all listener lifecycle notifications into unified internal dispatch helpers inside `WorkflowRunner`:
- `_adispatch_listeners(event_name, request, output, error)`: Asynchronous dispatch used across `aexecute()` and `stream()`.
- `_dispatch_listeners(...)`: Synchronous counterpart delegating to `run_async_safely` when `execute()` is called in synchronous environments.

Dispatch semantics across all execution modes (`execute`, `aexecute`, `stream`):
- **`on_completed`**: Awaited sequentially across all registered listeners. Exceptions are caught and logged as warnings (fail-open invariant) so transient side-effect errors never block response delivery.
- **`on_background_task`**: Scheduled concurrently via `asyncio.create_task` (or the persistent background concurrency loop) with GC-shielding sets.
- **`on_error`**: Awaited across all registered listeners whenever an unhandled execution exception occurs, before error handling or re-raising.
- **`on_cancelled`**: Awaited across listeners when streaming connections abort.

### 2. Prune Bypass Methods and Mutable Setters
- Remove `_apersist_turn`, `_persist_turn_sync`, and `_safe_evaluate` from `WorkflowRunner`. All write-side persistence and judge evaluations are handled exclusively by `MemoryPersistenceListener` and `OnlineEvaluationListener`.
- Replace mutable setter methods (`runner.memory = ...`, `runner.evaluator = ...`, `runner.eval_task_fn = ...`) with an immutable `listeners: Sequence[ExecutionLifecycleListener]` seam configured during construction.
- Accept `memory`, `evaluator`, and `eval_task_fn` strictly as convenience factory arguments in `__init__` and `get_workflow_runner` to build default `[MemoryPersistenceListener, OnlineEvaluationListener]` instances when `listeners` is omitted.

### 3. Read-Path Memory Hydration Boundary
- Retain `memory: ConversationMemoryAdapter | None` in `WorkflowRunner` strictly as a read-path dependency for input turn hydration in `_prepare_execution`.
- Write-path turn persistence belongs exclusively to `MemoryPersistenceListener`.

### 4. Transport-Agnostic Execution Seam
- Remove `background_tasks: BackgroundTasks | None` from `WorkflowRunner.execute`, `execute_with_context`, and `aexecute`.
- Background task dispatching relies exclusively on native `asyncio.create_task` on active or background loops with GC tracking sets.
- Clean up `POST /api/chat` route signature to remove unused `background_tasks: BackgroundTasks`.

---

## Consequences

- **Locality**: Post-execution side-effects (database turn writes, judge model scoring) concentrate exclusively in listener implementations rather than runner execution loops.
- **Leverage**: All execution modes (`execute`, `aexecute`, `stream`) share identical lifecycle events, error logging, and fail-open guarantees.
- **Testability**: Unit tests and benchmarking scripts can disable all persistence and evaluation side-effects by passing `listeners=[]`, without needing database locks or mock `BackgroundTasks` objects.
