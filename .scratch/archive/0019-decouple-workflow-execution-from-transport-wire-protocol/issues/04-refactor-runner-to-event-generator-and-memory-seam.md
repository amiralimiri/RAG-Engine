# 04: Refactor WorkflowRunner to Pure Event Generator & Explicit Memory

**What to build:**
Refactoring `WorkflowRunner` in `app/rag/workflows/runner.py` into a deep, transport-agnostic execution engine whose `stream()` yields `WorkflowExecutionEvent` instances, delegating side-effects to lifecycle listeners and making memory handling strictly explicit with zero implicit SQLite fallbacks and zero imports from `app/api/`.

**Blocked by:**
02: Execution Lifecycle Listener Protocol & Concrete Adapters

**Status:** closed

- [x] Remove all wire-level SSE imports, helpers, and `yield self.format_sse(...)` calls from `WorkflowRunner.stream()`.
- [x] Refactor `WorkflowRunner.stream()` to yield typed `StepTransitionEvent`, `TokenDeltaEvent`, and `ExecutionCompletedEvent`.
- [x] Wrap graph stream execution in error handling to invoke `on_error` and yield `ExecutionFailedEvent(error=str(exc))`.
- [x] Handle `asyncio.CancelledError` and `GeneratorExit` by invoking `on_cancelled` and flushing telemetry without saving incomplete turns.
- [x] Support pluggable `listeners: list[ExecutionLifecycleListener] | None = None` in `WorkflowRunner.__init__`, defaulting to `MemoryPersistenceListener` and `OnlineEvaluationListener` when memory/evaluator are configured.
- [x] Make memory injection strictly explicit: if `memory is None` or `isinstance(memory, NoOpMemoryStore)`, completely bypass pre-execution hydration and post-execution persistence.
- [x] Enforce zero imports from `app/api/` in `app/rag/workflows/runner.py`.
- [x] Write domain execution tests in `tests/rag/test_workflow_execution.py` verifying typed event sequences and listener behavior.
