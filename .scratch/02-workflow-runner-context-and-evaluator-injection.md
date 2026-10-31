# 02: WorkflowRunner Execution Context & Evaluator Injection

**What to build:**
Public domain execution context on `WorkflowRunner` allowing callers to retrieve both the assistant's `ChatResponse` and normalized `EvalContext` without violating encapsulation, and direct injection of `evaluator: Evaluator` into `WorkflowRunner` to manage online background evaluation.

**Blocked by:** 01: Core Evaluator Seam and Lazy Judge Lifecycle

**Status:** ready-for-agent

- [ ] `ExecutionResult` model (`response: ChatResponse`, `eval_context: EvalContext`) defined in `app/rag/workflows/runner.py`
- [ ] `WorkflowRunner.execute_with_context(request: ChatRequest, ...) -> ExecutionResult` public method implemented
- [ ] `WorkflowRunner.__init__` accepts `evaluator: Evaluator | None = None`, defaulting to `get_evaluator()`
- [ ] `_finalize_execution` and streaming pipelines delegate evaluation to `self.evaluator.aevaluate(...)` (or `evaluate(...)`)
- [ ] Comprehensive unit tests in `tests/rag/test_workflow_runner.py` verifying context extraction and evaluator injection with `FakeEvaluator`
