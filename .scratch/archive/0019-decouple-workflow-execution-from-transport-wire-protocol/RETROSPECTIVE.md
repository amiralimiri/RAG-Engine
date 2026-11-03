# Retrospective: ADR-0019 Decoupling Workflow Execution from Transport Wire Protocol

**Milestone:** ADR-0019 (Tickets 01–05)  
**Date:** 2026-11-03  
**Scope:** `app/rag/workflows/` (events, listeners, runner), `app/api/` (streaming adapter, routes), and downstream regression test suites.  
**Test Baseline:** 819 passed tests across the entire repository.

---

## 1. What Went Well

### 1.1. Exemplary TDD Cadence & Comprehensive Seam Isolation
- **Ticket-by-Ticket Grounding:** Each ticket followed strict test-first development. The domain event models (`events.py`), the lifecycle protocol (`listeners.py`), the transport presentation layer (`streaming.py`), and the runner decoupling (`runner.py`) were developed with dedicated test doubles and isolated unit tests before route integration.
- **Contract Integrity:** Introducing strongly typed domain events (`StepTransitionEvent`, `TokenDeltaEvent`, `ExecutionCompletedEvent`, `ExecutionFailedEvent`) allowed unit tests in `tests/rag/test_workflow_execution.py` to assert against structured object properties rather than fragile regex matching against SSE wire chunks.
- **Zero Regressions Across 819 Tests:** Refactoring the central engine of the application (`WorkflowRunner`) without breaking backward-compatible endpoints (`/api/chat/stream`, `/api/chat`, memory sessions, or frontend streaming parsers) proved the robustness of the seam design.

### 1.2. Zero Reverse Coupling & Architectural Cleanliness
- **Strict Dependency Direction:** Verified zero imports of `app/api/**` inside `app/rag/**`. The execution engine is completely transport-agnostic and ignorant of HTTP, Server-Sent Events, and JSON framing.
- **Clean Adapter Protocol:** The `SseStreamAdapter` in `app/api/streaming.py` acts as a pure presentation layer translating `AsyncIterable[WorkflowExecutionEvent]` into client-facing SSE frames without leaking wire concerns into the RAG runtime.

### 1.3. True Pluggability & Headless Execution
- **Zero SQLite Overhead for Benchmarking:** Explicit memory contracts (`memory=None` or `NoOpMemoryStore`) now completely disable SQLite lookups and writes, enabling lightning-fast headless evaluations (`run_experiment.py`) and matrix benchmarks without requiring database mocking or monkeypatching.
- **Safe Lifecycle Exception Shielding:** All listener notifications (`on_completed`, `on_background_task`, `on_error`, `on_cancelled`) are wrapped with exception shielding and structured logging, guaranteeing that auxiliary observability or persistence faults never crash the user's primary execution stream.

---

## 2. Friction Points & Root Causes

### 2.1. File Size & Context Pressure During Ticket 04 Refactoring
- **Symptom:** Refactoring `app/rag/workflows/runner.py` (~1,300 lines) required multiple chunked read operations and fine-grained patch insertions. Large monolithic files increase cognitive and token load, elevating the risk of token exhaustion or context truncation.
- **Root Cause:** While ADR-0019 extracted events, listeners, and SSE formatting, `WorkflowRunner` still acts as an umbrella class containing workflow lookup, execution profiling, Langfuse telemetry extraction, sync/async fallback adapters, and error recovery.

### 2.2. Sync/Async Lifecycle Listener Inconsistency
- **Symptom:** The initial Ticket 04 implementation wired `ExecutionLifecycleListener` into the streaming pipeline (`stream()`), but left sync/async execution (`execute()` / `aexecute()`) using legacy direct persistence (`_persist_turn_sync`) and background task scheduling (`_safe_evaluate`).
- **Root Cause:** Focusing heavily on the streaming wire decoupling caused the non-streaming execution paths (`execute`/`aexecute`) to lag behind in adopting the unified listener lifecycle.

### 2.3. Agent Guidelines Doc-Sync Lag
- **Symptom:** Code changes in Ticket 04 altered streaming error behavior (yielding an `ExecutionFailedEvent` directly rather than generating synthetic fallback tokens), but `docs/agents/workflow-execution.md` (Rules 3 and 6) still described the pre-ADR-0019 contract until flagged in review.
- **Root Cause:** Architectural guidelines in `docs/agents/` were treated as post-cycle reference documentation rather than being updated atomically alongside the code commits in the same ticket.

---

## 3. Process & Environment Action Items

| Action Item | Area | Description | Implementation Strategy |
| :--- | :--- | :--- | :--- |
| **1. Subsystem Decomposition for Large Modules** | Architecture | Decompose `WorkflowRunner` further (e.g., extract telemetry attribution into `TelemetryContextBinder` and profile resolution into `ExecutionProfileResolver`). | Target ~400–600 lines max per module to eliminate agent context pressure in future refactors. |
| **2. Atomic Doc-Sync in Ticket Checklists** | Process | Mandate that any change altering a rule in `docs/agents/` must include the doc update in the *same commit/PR* as the code change. | Add explicit doc checklist item to `docs/tickets/` templates: `"Sync architectural rules in docs/agents/"`. |
| **3. Unified Execution Pipeline (DRY Sync/Async/Stream)** | Architecture | Route `execute()` and `aexecute()` through the same `self.listeners` notification seam used by `stream()`. | Create a shared internal `_notify_lifecycle(...)` helper that dispatches `on_completed` and `on_background_task` across all 3 execution modes. |
| **4. Pre-warmed Production DI Verification Test** | Testing | Add integration test asserting that the FastAPI production entrypoint (`app.main:app`) boots with production singletons wired without manual harness intervention. | Implemented via `test_lifespan_prewarms_production_workflow_runner` in `test_workflow_runner.py`. |
| **5. Linters & UP Flags CI Synchronization** | Tooling | Document the interaction between `pyupgrade` (PEP 696 / `UP043` default type parameter elision in Python 3.13+) and static analyzers (`pyright`) to prevent linting rework. | Update `CODING_STANDARDS.md` § 1 with explicit guidelines on `AsyncGenerator` type parameter conventions. |
