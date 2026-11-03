# 03: Prune Private Bypass Methods & Mutable Property Setters

**What to build:** Remove vestigial bypass methods (`_apersist_turn`, `_persist_turn_sync`, and `_safe_evaluate`) and mutable property setters (`memory`, `evaluator`, `eval_task_fn`, and `listeners`) from `WorkflowRunner`. Establish `self.listeners` as the definitive side-effect seam configured during initialization with backward-compatible defaults.

**Blocked by:** 02: Route Synchronous and Asynchronous Execution Through the Listener Seam

**Status:** closed

- [x] Delete `_apersist_turn`, `_persist_turn_sync`, and `_safe_evaluate` from `WorkflowRunner` in `app/rag/workflows/runner.py`.
- [x] Remove setter decorators on `runner.memory`, `runner.evaluator`, `runner.eval_task_fn`, and `runner.listeners`.
- [x] Make `runner.listeners -> list[ExecutionLifecycleListener]` and `runner.memory -> ConversationMemoryAdapter | None` read-only properties.
- [x] Retain `memory`, `evaluator`, and `eval_task_fn` in `WorkflowRunner.__init__` and `get_workflow_runner` solely as convenience factory parameters to auto-wire `[MemoryPersistenceListener, OnlineEvaluationListener]` when `listeners` is omitted.
- [x] Verify that initializing `WorkflowRunner(listeners=[])` strictly guarantees zero SQLite writes and zero background judge tasks during execution.
- [x] Update existing unit tests in `tests/rag/test_workflow_runner.py` and `tests/test_online_evaluation.py` to configure runners via constructor injection rather than mutating properties.
