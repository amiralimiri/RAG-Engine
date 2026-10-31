# Technical Specification: Consolidate Evaluation Engine behind an Evaluator Seam

- **ADR Reference:** `docs/adr/0013-consolidate-evaluation-engine-behind-evaluator-seam.md`
- **Target Version:** `0.13.0`
- **Status:** Completed
- **Triage Label:** `ready-for-agent`
- **Work Packages / Tickets:** [`docs/specs/0013-consolidate-evaluation-engine-behind-evaluator-seam-tickets.md`](./0013-consolidate-evaluation-engine-behind-evaluator-seam-tickets.md)

---

## 1. Problem Statement

Evaluation in the RAG system is currently fractured across disjoint modules and layers:
1. `app/services/evaluation.py` handles online trace scoring and background evaluation tasks during live API requests.
2. `evaluation/judge.py` eagerly instantiates DeepEval models (`LocalModel`, `TypeSafeModel`) on module import at the repository root, creating import-time network and environment side-effects.
3. `evaluation/run_experiment.py` duplicates identical DeepEval metric calculation code (for Faithfulness and Answer Relevancy) and pierces `WorkflowRunner`'s encapsulation by invoking private `_execute_with_state` to retrieve evaluation contexts.
4. Callers like `app/api/routes.py` (feedback endpoint) and `app/rag/workflows/runner.py` couple to ad-hoc module functions rather than a cohesive domain evaluator protocol seam.

This fragmentation produces redundant code, violates caller encapsulation, causes side effects during testing, and prevents live queries and offline benchmark experiments from sharing a single, reliable evaluation engine.

---

## 2. Solution

Consolidate all evaluation logic into a deep `app/rag/evaluation/` domain subsystem:
1. **Unified Evaluation Subsystem (`app/rag/evaluation/`)**:
   Establish evaluation as a first-class citizen alongside `knowledge` and `workflows`. Completely delete `app/services/evaluation.py` and `evaluation/judge.py`, updating callers directly without intermediate backward-compatibility shims.
2. **Deep `Evaluator` Protocol Seam**:
   Expose a cohesive interface providing synchronous (`evaluate`), asynchronous (`aevaluate`), and direct metric scoring (`score_metric`) backed by a shared metric calculation pipeline.
3. **Encapsulated Execution Context**:
   Add a public domain method `WorkflowRunner.execute_with_context(request) -> ExecutionResult` that bundles `ChatResponse` with `EvalContext`, eliminating private `_execute_with_state` access in `run_experiment.py` while keeping the transport `ChatResponse` schema pure.
4. **Lazy Judge Lifecycle & Test Double**:
   Instantiate DeepEval judge models lazily on first access. Provide `FakeEvaluator` for fast, offline unit testing without external LLM dependencies.
5. **Dependency Injection & Test Isolation**:
   Provide `get_evaluator()` and `reset_evaluator()`, wiring `reset_evaluator()` into the `tests/conftest.py` autouse fixture suite and injecting the evaluator directly into `WorkflowRunner`.

---

## 3. User Stories

1. **As an ML evaluation engineer**, I want offline benchmark experiments (`run_experiment.py`) to execute through the identical metric evaluation pipeline used in production live queries, so that benchmark scores accurately reflect production behavior.
2. **As an evaluation engineer**, I want to invoke `WorkflowRunner.execute_with_context()` to obtain both the assistant's answer and the evaluation context without piercing private runner methods (`_execute_with_state`).
3. **As an API client developer**, I want `ChatResponse` to remain focused on user transport concerns without exposing internal retrieval contexts and evaluation metadata.
4. **As an API route developer**, I want `POST /api/feedback` to submit trace metric scores through a standard `get_evaluator().score_metric(...)` seam rather than an ad-hoc service function.
5. **As an observability engineer**, I want live chat requests and streaming responses to dispatch online evaluation through a shielded, asynchronous `aevaluate(...)` call, ensuring that judge timeouts or Langfuse rate limits never fail a user chat session.
6. **As a core engine developer**, I want LLM judge models (`LocalModel`, `TypeSafeModel`) to instantiate lazily on first use rather than on module import, preventing spurious network calls or configuration crashes when importing evaluation modules.
7. **As a test author**, I want to inject `FakeEvaluator` into `WorkflowRunner` and `get_evaluator()` during tests, so that test suites run completely offline with deterministic metrics and zero latency.
8. **As a test maintainer**, I want `reset_evaluator()` to automatically reset the active singleton between test cases via `tests/conftest.py`, ensuring total test isolation without monkeypatching global module dictionaries.

---

## 4. Implementation Decisions

### 4.1. Module Architecture & File Layout

The new subsystem lives in `app/rag/evaluation/`:
- `app/rag/evaluation/base.py`:
  - `EvaluationResult`: Pydantic data model encapsulating `faithfulness`, `answer_relevancy`, `scores: dict[str, float]`, and `details: dict[str, Any]`.
  - `Evaluator`: Strongly-typed `runtime_checkable` `Protocol` seam defining `evaluate`, `aevaluate`, and `score_metric`.
- `app/rag/evaluation/judge.py`:
  - `JudgeModels`: Encapsulates `LocalModel` and `TypeSafeModel` instances with thread-safe lazy initialization.
  - `get_judge_models()`: Factory returning the lazy singleton.
- `app/rag/evaluation/evaluator.py`:
  - `EvaluationEngine`: Production implementation satisfying `Evaluator`. Handles sampling gates (`eval_sample_rate`), metric computation via DeepEval, and Langfuse trace score dispatch.
  - `FakeEvaluator`: In-memory test double satisfying `Evaluator` with configurable scores and recorded calls.
  - `get_evaluator(...)` / `reset_evaluator()`: Factory and lifecycle functions with dependency injection overrides.
- `app/rag/evaluation/__init__.py`:
  - Clean exports: `Evaluator`, `EvaluationResult`, `EvaluationEngine`, `FakeEvaluator`, `get_evaluator`, `reset_evaluator`.

### 4.2. Seam Interfaces & Signatures

#### Evaluator Protocol Seam (`app/rag/evaluation/base.py`)

```python
class EvaluationResult(BaseModel):
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)

@runtime_checkable
class Evaluator(Protocol):
    def evaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
    ) -> EvaluationResult:
        """Synchronously measure metrics and optionally push scores to Langfuse."""
        ...

    async def aevaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
    ) -> EvaluationResult:
        """Asynchronously measure metrics with threadpool offloading and push scores."""
        ...

    def score_metric(
        self,
        trace_id: str,
        metric_name: str,
        score: float,
        comment: str | None = None,
    ) -> bool:
        """Submit a normalized score (0.0 to 1.0) and comment for a trace to Langfuse."""
        ...
```

#### WorkflowRunner Context Execution Seam (`app/rag/workflows/runner.py`)

```python
class ExecutionResult(BaseModel):
    response: ChatResponse
    eval_context: EvalContext

class WorkflowRunner:
    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        *,
        evaluator: Evaluator | None = None,
        callback_factory: Callable[..., Any] | None = None,
        trace_extractor: Callable[[Any], str | None] | None = None,
        telemetry_flusher: Callable[[], None] | None = None,
    ) -> None:
        ...

    def execute_with_context(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Public execution entrypoint returning both ChatResponse and normalized EvalContext."""
        ...
```

### 4.3. Elimination of Legacy Files (Zero-Shim Standard)
- Permanently remove `app/services/evaluation.py`.
- Permanently remove `evaluation/judge.py`.
- Update `app/api/routes.py`:
  - Replace `score_trace_metric` with `get_evaluator().score_metric(...)`.
- Update `evaluation/run_experiment.py`:
  - Replace `runner._execute_with_state` with `runner.execute_with_context`.
  - Replace duplicated `FaithfulnessMetric` and `AnswerRelevancyMetric` code with `evaluator.evaluate(...)`.
- Update all existing unit and integration tests importing from `app.services.evaluation` or `evaluation.judge` to use `app.rag.evaluation`.

---

## 5. Testing Decisions

1. **Test External Behavior at Seams**:
   Tests must cross the public `Evaluator` protocol seam (`evaluate`, `aevaluate`, `score_metric`) and the public `WorkflowRunner.execute_with_context` seam, without asserting against internal DeepEval objects or monkeypatching module dictionaries.
2. **Modules Under Test**:
   - `app/rag/evaluation/`: Test protocol conformance, lazy judge initialization, metric calculation, error shielding, and Langfuse score formatting.
   - `app/rag/workflows/runner.py`: Verify that `execute_with_context` returns valid `ExecutionResult`, and that `_finalize_execution` delegates to `evaluator.aevaluate(...)`.
   - `app/api/routes.py`: Verify that `/api/feedback` calls `evaluator.score_metric`.
   - `evaluation/run_experiment.py`: Verify that experiment executions use `execute_with_context` and `evaluator.evaluate` cleanly.
3. **Prior Art in the Codebase**:
   - `tests/rag/test_knowledge_store.py`: Protocol conformance and adapter testing for `VectorStoreAdapter`.
   - `tests/rag/test_workflow_runner.py`: Dependency injection and autouse singleton reset testing.
   - `tests/test_feedback_api.py`: Route-level telemetry scoring assertions.

---

## 6. Out of Scope

- Changing the underlying DeepEval library to an alternative evaluation framework (e.g. Ragas).
- Modifying prompt templates used in RAG generation or query rewriting.
- Adding new evaluation metrics beyond Faithfulness and Answer Relevancy (such as Hallucination or Toxicity).
- Changing the HTTP payload structure of `ChatRequest`, `ChatResponse`, or `FeedbackRequest`.

---

## 7. Further Notes

- `reset_evaluator()` must be added to the autouse fixture in `tests/conftest.py` alongside `reset_workflow_runner()` and `reset_workflow_registry()` to ensure consistent test isolation across the full suite.
- DeepEval metrics in `EvaluationEngine` should handle both modern DeepEval versions and older signatures gracefully (e.g. `metric.measure(tc, _show_indicator=False)` with fallback to `metric.measure(tc)`).
