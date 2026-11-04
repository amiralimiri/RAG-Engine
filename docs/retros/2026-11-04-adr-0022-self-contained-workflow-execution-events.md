# Retrospective: ADR-0022 Self-Contained Workflow Execution Events & Pure Transport Adapters

**Milestone:** ADR-0022 (Tickets 01–04)  
**Date:** 2026-11-04  
**Commits Reviewed:** `caf6b53` $\to$ `be04a99` (against fixed point `0e3f30e`)  
**Scope:** `app/rag/workflows/events.py`, `app/rag/workflows/runner.py`, `app/api/streaming.py`, `app/api/routes.py`, `tests/rag/test_workflow_events.py`, `tests/rag/test_workflow_runner.py`, `tests/api/test_sse_stream_adapter.py`, `tests/test_chat_stream.py`.  
**Test Baseline:** 873 passed tests across the entire test suite (0 failures, 100% regression green).

---

## 1. What Went Well

### 1.1. Zero-Shim Presentation Decoupling
- **Purged Wire Leaks**: Successfully eliminated the 19-line workflow definition lookup and exception handling block in `POST /api/chat/stream` (`app/api/routes.py`). The presentation route now collapses to a clean 8-line delegation:
  ```python
  return StreamingResponse(
      SseStreamAdapter.format(runner.stream(wf_input)),
      media_type="text/event-stream; charset=utf-8",
      headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
  )
  ```
- **Strict Zero-Shim Architecture Compliance**: Completely removed the `step_labels` parameter from `SseStreamAdapter.format` without introducing backward-compatibility wrapper shims or fallback parameter dictionaries, strictly obeying `docs/agents/codebase-unification.md` §1.1.

### 1.2. Self-Contained Domain Execution Events
- **Enriched Milestone Schema**: Enhanced `StepTransitionEvent` with `label: str` and `timestamp: float` (POSIX timestamp in seconds via `datetime.now(UTC).timestamp()`).
- **Resilient Fallback Validator**: Implemented an after-validation model validator (`@model_validator(mode="after")`) ensuring that empty or whitespace-only labels automatically fall back to `step`, guaranteeing that event consumers and test fakes never receive empty labels.
- **Single Authority for Step Label Resolution**: Established `WorkflowRunner._stream_generator` as the sole authority for resolving step labels against active workflow metadata (`wf_def.info.step_labels` or `DEFAULT_STEP_LABELS`), ensuring downstream transports (SSE, WebSockets, CLI) receive uniformly hydrated milestone events.

### 1.3. Clean Incremental Ticket Progression
- Progressed sequentially through 4 atomic tickets (Domain Model $\to$ Runner Emission $\to$ Pure Adapter $\to$ Route Collapse & Regression). Each ticket verified its own seam before moving to the next layer, resulting in zero regressions across 873 tests.

---

## 2. Friction Points & Root Causes

### 2.1. Conflation of Grilling / Design with Implementation
- **Symptom:** During design and stress-testing phases (e.g. `/grill-with-docs` or ADR drafting), there is an urge to immediately begin editing application code (`app/**`) or tests (`tests/**`) before the spec and ticket breakdown are finalized.
- **Root Cause:** Absence of an explicit phase-gating invariant in `AGENTS.md` strictly separating the design/grilling phase from the execution phase.
- **Resolution:** Formulated the Phase Gating Invariant for `AGENTS.md` forbidding production codebase modifications during design, brainstorming, and grilling loops.

### 2.2. Test Helper Naming Ambiguity
- **Symptom:** In `tests/rag/test_workflow_runner.py`, a test helper was named `parse_sse_events` despite parsing domain in-memory objects rather than wire-format text frames.
- **Root Cause:** Historical evolution of tests where SSE formatting originally took place in the runner before ADR-0019 event decoupling.
- **Resolution:** Renamed `parse_sse_events` to `normalize_execution_events` to reflect its true domain role and distinguish it from wire-level parsers like `parse_sse_lines` in `tests/test_chat_stream.py`.

---

## 3. Process & Environment Action Items

| Action Item | Area | Description | Implementation Strategy |
| :--- | :--- | :--- | :--- |
| **1. Enforce Phase Gating Invariant** | Process | Strictly forbid modifying application code (`app/**`, `tests/**`) during `/grill-with-docs` or ADR drafting. Only design artifacts (`docs/adr/`, `docs/specs/`, `GLOSSARY.md`) may be created or edited. | Codified in `AGENTS.md`. |
| **2. Self-Contained Domain Event Invariant** | Architecture | Ensure all domain execution events carry self-contained presentation metadata so presentation adapters remain pure transformers. | Documented in `docs/agents/workflow-execution.md` § 11. |
| **3. Distinguish Wire Parsers from Domain Normalizers** | Testing | Differentiate helpers that parse raw wire text (`parse_sse_lines`) from helpers that summarize in-memory domain events (`normalize_execution_events`). | Refactored in `tests/rag/test_workflow_runner.py`. |
| **4. Pure Transport Adapters** | Architecture | Transport adapters (`SseStreamAdapter`) must accept only `AsyncIterable[WorkflowExecutionEvent]` without external hydration parameters. | Codified in `docs/agents/workflow-execution.md` § 11. |
