# 03: Concrete Step Implementations

**What to build:**
Concrete step implementations encapsulating retrieval, classification, routing, prompt compilation, and query reformulation in `app/rag/steps/`.
Extracts logic for retrieval, grading, routing, and prompt generation into dedicated, reusable step classes:
- `ToolRetrievalStep` (`app/rag/steps/retrieval.py`, `@register_step("tool_retrieval")`): wraps execution of an injected `RAGTool`, runs `aexecute`, extracts `ToolEvidenceArtifact`, aggregates evidence, and updates `retrieved_contexts`, `citations`, and `source_used`.
- `ClassifierGradingStep` and `ClassifierRouteStep` (`app/rag/steps/classifier.py`, `@register_step("classifier_grade")`, `@register_step("classifier_route")`): wraps `WorkflowClassifier` seam invocations for routing and grading (`grade_kb`, `grade_web`).
- `PromptGenerationStep` and `QueryReformulationStep` (`app/rag/steps/generation.py`, `@register_step("prompt_generate")`, `@register_step("query_reformulate")`): compiles prompts from `PromptStore`, invokes `BaseChatModel`, handles query condensation and query rewriting, and synthesizes answers.

**Blocked by:**
02: Core Step Seam & Catalog Infrastructure

**Status:**
ready-for-agent

- [x] Implement `ToolRetrievalStep` in `app/rag/steps/retrieval.py` supporting injected tool, query extraction, evidence aggregation, and state emission.
- [x] Implement `ClassifierRouteStep` and `ClassifierGradingStep` in `app/rag/steps/classifier.py` supporting `WorkflowClassifier` methods.
- [x] Implement `PromptGenerationStep` in `app/rag/steps/generation.py` supporting chat history formatting, `PromptStore` rendering, LLM invocation, and answer extraction.
- [x] Implement `QueryReformulationStep` in `app/rag/steps/generation.py` supporting condensation and rewriting modes.
- [x] Register all step classes in `StepCatalog` with canonical aliases (`tool_retrieval`, `classifier_route`, `classifier_grade`, `prompt_generate`, `query_reformulate`).
- [x] Re-export steps cleanly from `app/rag/steps/__init__.py`.
- [x] Add unit test suites in `tests/rag/steps/test_retrieval_step.py`, `tests/rag/steps/test_classifier_steps.py`, and `tests/rag/steps/test_generation_steps.py` using offline test doubles (`FakeChatModel`, `FakeRAGTool`, `FakeWorkflowClassifier`, `FakePromptStore`).
- [x] Pass `ruff check` and `pyright` on modified modules.
