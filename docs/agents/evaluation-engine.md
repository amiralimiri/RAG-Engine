# Evaluation Engine & Evaluator Seam Patterns

Guidelines and codified agent rules from ADR-0013 (Consolidate Evaluation Engine behind an Evaluator Seam) for evaluation protocols, lazy judge lifecycles, execution context retrieval, offline test doubles, and observability scoring.

---

## 1. Core Principles

1. **Protocol Seam Architecture (`Evaluator`)**:
   - All evaluation tasks—including synchronous benchmark experiments, asynchronous server request telemetry, and user feedback submission—must flow through the strongly-typed `Evaluator` protocol seam (`app/rag/evaluation/base.py`).
   - The seam exposes three core methods:
     - `evaluate(question, answer, source_used, retrieved_contexts, trace_id=None) -> EvaluationResult` (synchronous measurement).
     - `async aevaluate(...) -> EvaluationResult` (asynchronous measurement with threadpool offloading).
     - `score_metric(trace_id, metric_name, score, comment=None) -> bool` (normalized trace scoring in Langfuse).
   - Domain modules (`app/rag/**`) and experiments (`evaluation/**`) must never import DeepEval metrics directly or invoke ad-hoc scoring functions.

2. **Encapsulated Execution Context (`execute_with_context`)**:
   - Callers needing both assistant responses and retrieval context (such as `evaluation/run_experiment.py`) must call `WorkflowRunner.execute_with_context(request) -> ExecutionResult`.
   - `ExecutionResult` cleanly encapsulates `response: ChatResponse` and `eval_context: EvalContext`.
   - **Never** pierce private runner methods (e.g. `_execute_with_state`) or mutate Pydantic models (e.g. dynamically attaching `chat_response._raw_state`).
   - Transport handlers (`POST /api/chat`, `POST /api/chat/stream`) strictly consume `execute()` or `stream()` to prevent leaking internal retrieval contexts to API clients.

3. **Decoupled Metric Calculation vs. Score Dispatch Gating**:
   - Evaluation metric computation must **never** be gated on `eval_sample_rate > 0.0`.
   - Metric calculation is governed strictly by `settings.eval_metrics` (`["faithfulness", "answer_relevancy"]`) and whether relevant contexts are present.
   - Remote dispatch to Langfuse is governed by `trace_id`, `settings.langfuse_enabled`, and `settings.eval_sample_rate`.
   - This ensures offline benchmark runs and evaluation test suites accurately calculate metric scores even when sample rate is set to 0.0 or Langfuse is disabled.

4. **Lazy Judge Lifecycle (`JudgeModels`)**:
   - DeepEval judge models (`LocalModel`, `TypeSafeModel`) must **never** be initialized at module import time.
   - Use the lazy singleton provider `get_judge_models()` in `app/rag/evaluation/judge.py`, which instantiates LLM models only when metric evaluation is explicitly executed.
   - This prevents unnecessary network calls, environment variable errors, and import slowdowns when running unrelated tools or unit tests.

5. **100% Offline-Capable Test Doubles (`FakeEvaluator`)**:
   - Unit and integration tests must run 100% offline with zero external network or LLM dependencies.
   - Use `FakeEvaluator` (`app/rag/evaluation/evaluator.py`) to provide deterministic metric scores (`faithfulness`, `answer_relevancy`, `scores`) and record invocation history.
   - The global evaluator singleton is automatically reset between tests via the autouse `clean_evaluator` fixture in `tests/conftest.py`.

6. **Zero-Shim Domain Architecture**:
   - Never retain deprecated modules or backward-compatibility wrappers (`app/services/evaluation.py` and `evaluation/judge.py` are permanently deleted).
   - Direct callers (such as `POST /api/feedback`) must invoke the canonical domain seam (`get_evaluator().score_metric(...)`).

7. **Langfuse Configuration & Base URL Harmonization**:
   - Always support both `LANGFUSE_HOST` and `LANGFUSE_BASE_URL` environment variables using `pydantic.AliasChoices` in `Settings` (`app/core/config.py`).
   - Access the normalized URL through `settings.langfuse_host` or `settings.langfuse_base_url` (which trims trailing slashes).
   - Always pass `settings` explicitly to telemetry helpers (e.g., `get_langfuse_client(settings)`).

---

## 2. Invariants & Guardrails

| Invariant | Enforcement Pattern | Prohibited Pattern |
|---|---|---|
| **Seam Entrypoint** | `get_evaluator().evaluate(...)` or `active_runner.evaluator.aevaluate(...)` | Direct instantiation of `FaithfulnessMetric()` or `AnswerRelevancyMetric()` in callers |
| **Context Extraction** | `runner.execute_with_context(req) -> ExecutionResult` | Calling private `runner._execute_with_state(req)` or attaching `_raw_state` to responses |
| **Online Shielding** | Background online evaluation in `runner.py` must run asynchronously via `run_in_threadpool` or `aevaluate` and catch exceptions gracefully | Awaiting synchronous judge evaluation in HTTP request threads or allowing eval errors to fail chat responses |
| **Metric Calculation Gating** | `eval_metrics` determines calculation; `eval_sample_rate` determines Langfuse score dispatch | `if self.settings.eval_sample_rate > 0.0:` wrapping metric computation |
| **Test Double Seeding** | `get_evaluator(FakeEvaluator(faithfulness=0.95))` | Patching module dictionaries or monkeypatching `EvaluationEngine._compute_metrics` |
| **Test Teardown Isolation** | `reset_evaluator()` in autouse fixture `tests/conftest.py:clean_evaluator` | Leaving overridden evaluators alive across test boundaries |

---

## 3. Quick Reference

### Running Evaluation in Batch / Experiments:
```python
from app.api.schemas import ChatRequest
from app.rag.evaluation import get_evaluator
from app.rag.workflows.runner import get_workflow_runner

runner = get_workflow_runner()
evaluator = get_evaluator()

exec_result = runner.execute_with_context(ChatRequest(question="How do I connect to VPN?"))
eval_result = evaluator.evaluate(
    question="How do I connect to VPN?",
    answer=exec_result.response.answer,
    source_used=exec_result.eval_context.source_used,
    retrieved_contexts=exec_result.eval_context.retrieved_contexts,
)
print(f"Faithfulness: {eval_result.faithfulness}, Relevancy: {eval_result.answer_relevancy}")
```

### In-Memory Testing with `FakeEvaluator`:
```python
from app.rag.evaluation import FakeEvaluator, get_evaluator

fake_eval = FakeEvaluator(faithfulness=1.0, answer_relevancy=0.9)
get_evaluator(fake_eval)  # Injects fake double

# Run tests...
assert fake_eval.call_count == 1
assert fake_eval.last_call["question"] == "How do I connect to VPN?"
```
