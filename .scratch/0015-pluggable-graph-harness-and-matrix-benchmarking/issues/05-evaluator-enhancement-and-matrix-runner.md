# 05: Supervised Evaluator Seam Enhancement & Matrix Benchmarking Runner

**What to build:**
Upgrade the `Evaluator` seam and benchmark experiment runner for offline matrix testing:
1. Update `Evaluator` protocol in `app/rag/evaluation/base.py`:
   - `evaluate(..., *, expected_output: str | None = None, expected_source: str | None = None)`
   - `aevaluate(..., *, expected_output: str | None = None, expected_source: str | None = None)`
2. Update `EvaluationEngine` and `FakeEvaluator` in `app/rag/evaluation/evaluator.py`:
   - When `expected_source` is provided, compute `source_accuracy` (1.0 if match, 0.0 otherwise) and record to Langfuse.
   - When `expected_output` is provided, compute semantic reference correctness or store in evaluation details.
3. Update `evaluation/run_experiment.py`:
   - Add CLI parameter `--matrix <path.yaml>`.
   - Support loading a YAML matrix file with multiple workflow/profile definitions.
   - For each matrix entry, execute benchmark runs using `runner.execute_with_context(workflow_input)`.
   - Link dataset items to experiment runs formatted as `<workflow_id>@<variant_id>--<timestamp>`.
   - Output formatted comparison tables displaying latency, faithfulness, relevancy, and source accuracy across variants.
4. Update `tests/test_experiment_runner.py` to test matrix benchmarking execution and scoring with `FakeEvaluator`.

**Blocked by:** 04: `ExecutionProfile` 3-Tier Resolution & Multi-Dimensional Telemetry

**Status:** open

- [ ] `Evaluator` protocol updated with optional `expected_output` and `expected_source`
- [ ] `FakeEvaluator` and `EvaluationEngine` support ground-truth scoring (`source_accuracy`)
- [ ] `evaluation/run_experiment.py` accepts `--matrix` YAML plans
- [ ] Experiment runs in Langfuse formatted as `<workflow_id>@<variant_id>--<timestamp>`
- [ ] Formatted comparison matrix table printed to console
- [ ] Unit & integration tests in `tests/test_experiment_runner.py` passing: `uv run pytest tests/test_experiment_runner.py`
