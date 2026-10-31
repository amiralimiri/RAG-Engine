# 04: WorkflowRunner Test Suite Modernization & Conformance Gate

**What to build:**
Modernize `tests/rag/test_workflow_runner.py` to target constructor-based hook injection rather than per-method hook arguments, add explicit unit tests for new singleton reset and error shielding invariants, and execute the full regression test suite as a final conformance gate.

**Blocked by:**
- 01: WorkflowRunner Constructor Auto-Wiring, Interface Shrinkage & Singleton Reset
- 02: Transport Layer Simplification (`app/api/routes.py`)
- 03: Telemetry, Contexts, and Experiment Caller Migration

**Status:** completed

### Acceptance Criteria
- [x] `tests/rag/test_workflow_runner.py` migrates tests from `runner.execute(..., callback_factory=...)` to `WorkflowRunner(callback_factory=...)`.
- [x] `tests/rag/test_workflow_runner.py` migrates tests from `runner.stream(..., callback_factory=...)` to `WorkflowRunner(callback_factory=...)`.
- [x] Add explicit unit tests asserting that:
  - Default `WorkflowRunner()` auto-wires canonical production hooks.
  - `reset_workflow_runner()` cleanly resets `_default_runner` singleton.
  - Evaluation exceptions in background tasks are caught, logged as warnings, and never abort execution or raise to caller.
  - `execute()` and `stream()` reject or ignore extraneous hook kwargs cleanly.
- [x] Full regression suite (`pytest`) runs with zero failures across the entire codebase.
