# 01: Core Evaluator Seam and Lazy Judge Lifecycle

**What to build:**
A unified evaluation domain package (`app/rag/evaluation/`) encapsulating judge models, metric evaluation (Faithfulness and Answer Relevancy), and Langfuse trace score dispatch behind a strongly-typed `Evaluator` protocol seam (`evaluate`, `aevaluate`, `score_metric`). Replaces eager root-level judge imports with lazy instantiation, provides an in-memory `FakeEvaluator` test double for deterministic testing, and provides `get_evaluator()` / `reset_evaluator()` factories wired into `tests/conftest.py`.

**Blocked by:** None (can start immediately)

**Status:** completed

- [x] `EvaluationResult` Pydantic model (`faithfulness`, `answer_relevancy`, `scores`, `details`) defined in `app/rag/evaluation/base.py`
- [x] `Evaluator` protocol seam (`evaluate`, `aevaluate`, `score_metric`) defined in `app/rag/evaluation/base.py`
- [x] Lazy `JudgeModels` factory implemented in `app/rag/evaluation/judge.py`, deferring DeepEval model instantiation until first use
- [x] `EvaluationEngine` implemented in `app/rag/evaluation/evaluator.py` supporting sampling gates, metric measurement, and Langfuse score submission
- [x] `FakeEvaluator` test double implemented in `app/rag/evaluation/evaluator.py` supporting configurable scores and recording call history
- [x] `get_evaluator()` and `reset_evaluator()` factories implemented in `app/rag/evaluation/evaluator.py`
- [x] `reset_evaluator()` registered in the `tests/conftest.py` autouse fixture suite
- [x] `app/rag/evaluation/__init__.py` exporting all public symbols cleanly
- [x] Comprehensive unit test suite in `tests/rag/test_evaluation_engine.py` passes with 100% green tests
