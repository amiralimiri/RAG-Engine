# 02: Implement Production Classifier Adapters (OpenAI & JEV)

**What to build:** Implement `OpenAIClassifierAdapter` and `JevClassifierAdapter` in `app/rag/classifiers.py` satisfying `WorkflowClassifier`. Encapsulate all TypeSafe JEV token estimation heuristics and Langfuse generation span creation inside `JevClassifierAdapter`.

**Blocked by:** 01: Define WorkflowClassifier Protocol Seam and Test Double

**Status:** completed

- [x] `OpenAIClassifierAdapter` implements `WorkflowClassifier` using `ChatOpenAI(temperature=0, stream_usage=True)` with `.with_structured_output(..., method="json_mode")`.
- [x] `OpenAIClassifierAdapter` binds prompts `"rag:router:llm"`, `"rag:grade_kb:llm"`, and `"rag:grade_web:llm"` with Langfuse metadata linking via `_build_prompt_config`.
- [x] `JevClassifierAdapter` implements `WorkflowClassifier` using `TypeSafeClassifier`.
- [x] `JevClassifierAdapter` encapsulates `_estimate_jev_tokens` and custom Langfuse generation span recording without leaking token heuristics to callers.
- [x] Unit tests with mocked LLM/JEV backends in `tests/test_workflow_classifiers.py` verify that both adapters produce valid `RouteDecision` and `EvidenceGrade` objects.
