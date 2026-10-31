# Retrospective: Milestone 0012 (Internalize Runtime Hooks into WorkflowRunner)

**Date:** October 31, 2026  
**Status:** Completed, Reviewed, & Archived  
**ADR Reference:** `docs/adr/0012-internalize-runtime-hooks-into-workflow-runner.md`  
**Specification:** `docs/specs/0012-internalize-runtime-hooks-into-workflow-runner-spec.md`  
**Work Packages / Tickets:** `docs/specs/0012-internalize-runtime-hooks-into-workflow-runner-tickets.md`  
**Test Suite:** 457 passed, 0 failed, 100% offline-capable  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0012 addressed interface bloat and parameter leakage in `WorkflowRunner`. Previously, `WorkflowRunner` exposed four runtime callback hooks (`callback_factory`, `trace_extractor`, `telemetry_flusher`, `eval_task_fn`) across all execution methods (`execute`, `aexecute`, `stream`, etc.), forcing callers like `app/api/routes.py` to construct and unpack boilerplate hook dictionaries (`_runner_hooks()`).

### Key Deliverables:
- **Constructor-Only Hook Binding**:
  - Bound all runtime hooks during `WorkflowRunner.__init__`, defaulting them directly to production adapters.
  - Removed duplicate hook arguments from `execute()`, `aexecute()`, and `stream()`.
- **Transport Layer Simplification**:
  - Deleted `_runner_hooks()` helper from `app/api/routes.py`.
  - Streamlined `/chat` and `/chat/stream` route handlers into concise 1-line runner invocations.
- **Test Isolation & Reset**:
  - Introduced `reset_workflow_runner()` and registered `clean_runner` in `tests/conftest.py` autouse fixture suite.
  - Migrated telemetry, context, and experiment callers to use the clean runner interface.

---

## 2. Artifact & Ticket Archive Status

All 4 work packages from `.scratch/tickets/` have been moved to `.scratch/archive/0012-internalize-runtime-hooks-into-workflow-runner/issues/`:

| Ticket | Scope | Status |
|---|---|---|
| **Ticket 01** | WorkflowRunner Constructor Auto-Wiring & Reset | Completed & Archived |
| **Ticket 02** | Transport Layer Simplification (`routes.py`) | Completed & Archived |
| **Ticket 03** | Telemetry, Contexts, and Experiment Caller Migration | Completed & Archived |
| **Ticket 04** | WorkflowRunner Test Modernization & Conformance Gate | Completed & Archived |
