# Retrospective: ADR-0020 Unify Workflow Execution Lifecycle and Side-Effect Dispatch

**Milestone:** ADR-0020 (Tickets 01–04)  
**Date:** 2026-11-04  
**Scope:** `app/rag/workflows/runner.py`, `app/rag/workflows/listeners.py`, `app/rag/workflows/base.py`, `app/api/routes.py`, `tests/rag/test_listener_dispatch.py`, `tests/rag/test_workflow_execution.py`, `tests/rag/test_workflow_runner.py`, `tests/api/test_routes_chat_memory.py`.  
**Test Baseline:** 841 passed tests across the entire repository (0 failures).

---

## 1. What Went Well

### 1.1. Complete Seam Unification Across Execution Pathways
- **Harmonized Side-Effect Dispatching**: Prior to this milestone, `stream()` executed side-effects via `ExecutionLifecycleListener`, whereas `execute()` and `aexecute()` bypassed listeners entirely via private persistence and evaluation helpers. ADR-0020 brought all three execution pathways (`execute`, `aexecute`, `stream`) onto a single, unified listener dispatch engine (`_adispatch_listeners` and `_dispatch_listeners` via `run_async_safely`).
- **Zero-Bypass Architecture**: Permanently deleted legacy private bypass helpers (`_apersist_turn`, `_persist_turn_sync`, `_safe_evaluate`) from `WorkflowRunner`. There is now only one path for turn persistence (`MemoryPersistenceListener`) and judge scoring (`OnlineEvaluationListener`).
- **Read-Path vs. Write-Path Decoupling**: Strictly separated conversational history hydration (input read-path in `_prepare_execution`) from SQLite turn persistence (output write-path in `MemoryPersistenceListener`).

### 1.2. Transport Decoupling & Modernized API Route
- **Eliminated Framework Leaks**: Successfully removed Starlette/FastAPI `background_tasks: BackgroundTasks` parameters from `WorkflowRunner.execute`, `execute_with_context`, and `aexecute`. Background judge evaluation relies exclusively on native `asyncio.create_task` concurrency with GC-shielding tracking sets.
- **Pure Declarative Route Signature**: Streamlined `POST /api/chat` in `app/api/routes.py` to a pure, declarative handler (`def chat(payload: ChatRequest) -> ChatResponse`) devoid of framework concurrency plumbing.

### 1.3. Clean Test Isolation & Headless Benchmarking
- **Zero Disk I/O Guarantees**: Verified that configuring `WorkflowRunner(listeners=[])` strictly executes with zero database operations and zero background judge tasks, unlocking headless benchmarking and isolated testing without monkeypatching.
- **Fail-Open Fault Isolation**: Defensively wrapped lifecycle dispatching in `try/except` handlers to ensure transient database or judge evaluation failures log warnings without disrupting response delivery to clients.
- **Domain Encapsulation**: Refactored heterogeneous chunk inspection logic into `WorkflowOutput.normalize_contexts()`, eliminating the Feature Envy smell and keeping the runner's lifecycle dispatcher focused solely on orchestration.

---

## 2. Friction Points & Root Causes

### 2.1. Dual-Axis Review Uncovered Edge Case Inconsistencies
- **Symptom:** The initial implementation had two subtle failure-mode discrepancies:
  1. A graph execution failure in `_execute_with_state` could trigger `on_error` during the graph invocation and then trigger a second `on_error` if fallback state handling or finalization also threw.
  2. The default auto-wiring in `WorkflowRunner.__init__` omitted `get_evaluator()` fallback when `evaluator=None` and `eval_task_fn=None`.
- **Root Cause:** Error handling logic was added incrementally across sync and async execution paths without an explicit single-dispatch guard flag (`error_dispatched = True`).
- **Resolution:** Added `error_dispatched` protection to both `_execute_with_state` and `_aexecute_with_state` and synced constructor auto-wiring to the exact spec line contract.

### 2.2. Test Fixture Habitual Monkeypatching
- **Symptom:** `tests/api/test_routes_chat_memory.py` monkeypatched `_default_runner` directly rather than invoking the canonical DI factory `get_workflow_runner(...)`.
- **Root Cause:** In earlier iterations of the codebase before ADR-0012, module-level globals were frequently patched with `pytest.MonkeyPatch`.
- **Resolution:** Replaced all private singleton monkeypatching with `get_workflow_runner(...)` and updated `docs/agents/codebase-unification.md` to explicitly forbid monkeypatching private runner singletons.

---

## 3. Process & Environment Action Items

| Action Item | Area | Description | Implementation Strategy |
| :--- | :--- | :--- | :--- |
| **1. Enforce Canonical DI Factory in All Route Tests** | Architecture / Testing | Ban monkeypatching of `_default_runner` across the codebase; rely on `get_workflow_runner(registry=..., memory=..., listeners=...)` and autouse `reset_workflow_runner()`. | Documented in `docs/agents/codebase-unification.md` § 2.2. |
| **2. Single-Dispatch Invariants for Lifecycle Hooks** | Architecture | Ensure lifecycle error hooks (`on_error`, `on_cancelled`) always maintain single-dispatch state flags to prevent duplicate observer notifications during cascade failures. | Standardized with `error_dispatched` pattern in `WorkflowRunner`. |
| **3. Domain Context Encapsulation** | Domain Modeling | Keep DTO parsing, chunk normalization, and structure conversions encapsulated within domain models (`WorkflowOutput.normalize_contexts()`). | Documented in `docs/agents/workflow-execution.md` § 9. |
| **4. Subsystem Decomposition for WorkflowRunner** | Refactoring Candidate | `WorkflowRunner` is ~1,230 lines; consider extracting telemetry context binding (`TelemetryContextBinder`) and execution profile parameter resolution (`ProfileParameterResolver`) in a future milestone. | Flagged for post-ADR-0020 backlog. |
