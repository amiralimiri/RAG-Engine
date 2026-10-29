# 01: Define WorkflowClassifier Protocol Seam and Test Double

**What to build:** Define the strongly-typed `WorkflowClassifier` protocol in `app/rag/classifiers.py` with discrete synchronous methods for `route`, `grade_kb`, and `grade_web`. Implement an in-memory `FakeClassifierAdapter` for hermetic testing.

**Blocked by:** None (can start immediately)

**Status:** completed

- [x] `WorkflowClassifier` is defined as a `@runtime_checkable` `typing.Protocol` in `app/rag/classifiers.py`.
- [x] Protocol declares synchronous methods:
  - `route(question: str, config: RunnableConfig | None = None) -> RouteDecision`
  - `grade_kb(question: str, current_query: str, kb_docs: list[Document], config: RunnableConfig | None = None) -> EvidenceGrade`
  - `grade_web(question: str, current_query: str, web_results: str, config: RunnableConfig | None = None) -> EvidenceGrade`
- [x] `FakeClassifierAdapter` implements `WorkflowClassifier` with configurable return values for `route`, `grade_kb`, and `grade_web`.
- [x] Unit tests in `tests/test_workflow_classifiers.py` verify that `FakeClassifierAdapter` satisfies `isinstance(adapter, WorkflowClassifier)`.
