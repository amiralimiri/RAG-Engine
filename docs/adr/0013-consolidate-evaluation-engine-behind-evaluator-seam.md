# ADR 0013: Consolidate Evaluation Engine behind an Evaluator Seam

## Status
Accepted (Extends ADR 0010, ADR 0011, and ADR 0012)

## Context
Evaluation in the system has been historically divided across disjoint layers:
1. `app/services/evaluation.py` handled online trace evaluation tasks and feedback scoring during live API requests.
2. `evaluation/judge.py` eagerly initialized DeepEval judge models (`LocalModel`, `TypeSafeModel`) on module import at the repository root.
3. `evaluation/run_experiment.py` duplicated identical metric calculation code for Faithfulness and Answer Relevancy, and pierced `WorkflowRunner`'s private interface (`_execute_with_state`) to obtain evaluation context (`source_used`, `retrieved_contexts`).

This fractured architecture produced import-time side effects, code duplication, and seam violations. Live requests and offline experiments evaluated the same LLM responses with duplicate code while breaking encapsulation.

---

## Decisions

### 1. Unified `app/rag/evaluation/` Subsystem
Elevate evaluation to a core first-class RAG domain subsystem in `app/rag/evaluation/` alongside `app/rag/knowledge/` and `app/rag/workflows/`. Completely delete `app/services/evaluation.py` and update all callers directly without intermediate shims.

### 2. Deep `Evaluator` Protocol Seam
Define a strongly-typed `Evaluator` protocol in `app/rag/evaluation/base.py`:
- `evaluate(question: str, answer: str, source_used: str, retrieved_contexts: list[str], trace_id: str | None = None) -> EvaluationResult` (synchronous, for batch CLI experiments and tests).
- `async aevaluate(question: str, answer: str, source_used: str, retrieved_contexts: list[str], trace_id: str | None = None) -> EvaluationResult` (asynchronous, using threadpool offloading for live server execution).
- `score_metric(trace_id: str, metric_name: str, score: float, comment: str | None = None) -> bool` (normalized metric scoring).

Both `evaluate` and `aevaluate` delegate to a single internal metric calculation pipeline that handles DeepEval test cases and metric execution identically across online and offline runs.

### 3. Encapsulated Execution Context on WorkflowRunner
To eliminate `run_experiment.py`'s dependence on private `_execute_with_state` without polluting the transport `ChatResponse` schema, `WorkflowRunner` provides a public domain execution method:
- `execute_with_context(request: ChatRequest, ...) -> ExecutionResult` where `ExecutionResult` encapsulates `response: ChatResponse` and `eval_context: EvalContext`.
`run_experiment.py` calls `execute_with_context()` and passes the resulting `eval_context` directly into `evaluator.evaluate(...)`.

### 4. Lazy Judge Provider and Offline Test Double
- Delete `evaluation/judge.py` completely.
- Introduce a lazy judge factory in `app/rag/evaluation/judge.py` that instantiates DeepEval models on first use rather than upon module import.
- Provide `FakeEvaluator`, an in-memory test double satisfying the `Evaluator` protocol with deterministic scores and zero network or DeepEval dependencies.

### 5. Dependency Injection & Test Isolation
- Expose `get_evaluator(...)` and `reset_evaluator()`.
- Incorporate `reset_evaluator()` into the autouse fixture suite in `tests/conftest.py`.
- Inject `evaluator: Evaluator` into `WorkflowRunner.__init__`, defaulting to `get_evaluator()`.
- Update `POST /api/feedback` to invoke `get_evaluator().score_metric(...)`.

---

## Consequences

### Positive
- **Strict Locality**: All evaluation metrics, test-case transformations, judge lifecycles, and Langfuse score dispatches concentrate in `app/rag/evaluation/`.
- **High Leverage**: A single interface serves both live request streaming evaluation and batch offline benchmark runs.
- **Seam Integrity**: Eliminates caller piercing into `WorkflowRunner._execute_with_state` while keeping transport schemas (`ChatResponse`) pure.
- **Zero Import Side Effects**: Eliminates eager LLM judge initialization on import.
- **Deterministic Testability**: Fast offline unit tests using `FakeEvaluator` without monkeypatching module globals.

### Negative / Trade-Offs
- DeepEval dependencies and metric thresholds are encapsulated inside `app/rag/evaluation/`; callers cannot inject raw arbitrary DeepEval metric instances without passing them through the evaluator configuration or adapter.
