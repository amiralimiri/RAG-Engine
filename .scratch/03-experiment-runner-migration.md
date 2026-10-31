# 03: Experiment Runner Migration & Metric Consolidation

**What to build:**
Update the benchmark experimentation runner (`evaluation/run_experiment.py`) to execute workflows via `WorkflowRunner.execute_with_context()`, completely eliminating private `_execute_with_state` calls, and delegate metric computation to the unified `Evaluator` seam instead of duplicating DeepEval test case logic.

**Blocked by:** 02: WorkflowRunner Execution Context & Evaluator Injection

**Status:** closed

- [x] `evaluation/run_experiment.py` calls `active_runner.execute_with_context(...)` rather than `_execute_with_state`
- [x] Inline `FaithfulnessMetric` and `AnswerRelevancyMetric` instantiation in `run_experiment.py` replaced by `evaluator.evaluate(...)`
- [x] Remove `judge` import from `run_experiment.py`
- [x] Update `tests/test_experiment_runner.py` to verify seamless experiment execution using `FakeEvaluator`
