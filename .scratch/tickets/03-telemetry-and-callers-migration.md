# 03: Telemetry, Contexts, and Experiment Caller Migration

**What to build:**
Update test suites and external experiment runners that previously invoked or mocked `execute_with_state` to use the internalized `_execute_with_state` helper or normalized `execute` method, maintaining full verification coverage across telemetry, context extraction, and offline experimentation.

**Blocked by:** 01: WorkflowRunner Constructor Auto-Wiring, Interface Shrinkage & Singleton Reset

**Status:** ready-for-agent

### Acceptance Criteria
- [ ] `evaluation/run_experiment.py` updates line 134 to call `active_runner._execute_with_state(req)` or `active_runner.execute(req)`.
- [ ] `tests/test_jev_telemetry.py` fixtures and tests migrate from `execute_with_state` to `_execute_with_state`.
- [ ] `tests/test_llm_telemetry.py` fixtures and tests migrate from `execute_with_state` to `_execute_with_state`.
- [ ] `tests/test_retrieved_contexts.py` line 319 migrates from `execute_with_state` to `_execute_with_state`.
- [ ] `tests/test_experiment_runner.py` mocks for `WorkflowRunner` align with the deepened runner interface (`execute` / `_execute_with_state`).
- [ ] All targeted telemetry and experiment test modules pass with zero errors.
