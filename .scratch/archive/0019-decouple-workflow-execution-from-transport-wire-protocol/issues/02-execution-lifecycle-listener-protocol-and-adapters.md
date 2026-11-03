# 02: Execution Lifecycle Listener Protocol & Concrete Adapters

**What to build:**
A pluggable event-driven lifecycle listener protocol (`ExecutionLifecycleListener`) that decouples post-execution side-effects from the execution engine, along with default no-op base behavior and production adapters for conversation turn persistence and online evaluation.

**Blocked by:**
01: Domain Event Subsystem & Schemas

**Status:** closed

- [x] Create `app/rag/workflows/listeners.py` defining:
  - `ExecutionLifecycleListener(Protocol)` with `on_completed(request, output)`, `on_background_task(request, output)`, `on_error(request, error)`, and `on_cancelled(request, partial_output)`.
  - `BaseExecutionLifecycleListener` implementing default no-op methods for all hooks.
  - `MemoryPersistenceListener`: Awaits atomic turn persistence (`memory.aappend_turn`) on `on_completed` when `session_id` and `answer` are present; discards partial turns on `on_cancelled`.
  - `OnlineEvaluationListener`: Dispatches judge model evaluation safely inside `on_background_task`.
- [x] Export listeners from `app/rag/workflows/__init__.py`.
- [x] Implement isolated unit tests in `tests/rag/test_workflow_listeners.py` verifying each listener's behavior under completion, background scheduling, errors, and cancellation using offline test doubles.
