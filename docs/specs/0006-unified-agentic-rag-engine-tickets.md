# Work Packages / Implementation Tickets: Unified Agentic RAG Workflow Engine & Classifier Seam

- **Specification Reference:** [`docs/specs/0006-unified-agentic-rag-engine-spec.md`](./0006-unified-agentic-rag-engine-spec.md)
- **ADR Reference:** [`docs/adr/0006-unified-agentic-rag-engine-and-classifier-seam.md`](../adr/0006-unified-agentic-rag-engine-and-classifier-seam.md)
- **Status:** Completed

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│ Ticket 01: Define WorkflowClassifier Protocol Seam     │
│ and Test Double (app/rag/classifiers.py)               │
└───────────────────────────┬────────────────────────────┘
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
┌─────────────────────────────────┐   ┌─────────────────────────────────┐
│ Ticket 02: Implement Production │   │ Ticket 03: Consolidate Deep     │
│ Adapters (OpenAI & JEV)         │   │ Workflow Engine with Retriever  │
│ (app/rag/classifiers.py)        │   │ Hook (app/rag/engine.py)        │
└───────────────┬─────────────────┘   └───────────────┬─────────────────┘
                │                                     │
                └──────────────────┬──────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 04: Backward-Compatibility Shims & Full E2E     │
│ Regression Verification (fix_single_llm & jev shims)   │
└────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Define `WorkflowClassifier` Protocol Seam & Test Double | `app/rag/classifiers.py`, `tests/test_workflow_classifiers.py` | None | `completed` |
| **Ticket 02** | Implement `OpenAIClassifierAdapter` & `JevClassifierAdapter` | `app/rag/classifiers.py`, `tests/test_workflow_classifiers.py` | Ticket 01 | `completed` |
| **Ticket 03** | Consolidate Deep `Workflow Engine` with Pluggable Retriever Hook | `app/rag/engine.py`, `tests/test_workflow_engine.py` | Ticket 01 | `completed` |
| **Ticket 04** | Wire Backward-Compatibility Shims & Verify Full Regression Suite | `app/rag/fix_single_llm.py`, `app/rag/fix_single_jev.py`, existing test suite | Ticket 02, Ticket 03 | `completed` |

---

## Ticket 01: Define `WorkflowClassifier` Protocol Seam & Test Double

- **Title:** Define `WorkflowClassifier` Protocol Seam and In-Memory Test Double
- **Target Files:**
  - `app/rag/classifiers.py`
  - `tests/test_workflow_classifiers.py`
- **Blockers:** None (can start immediately)
- **Status:** `completed`

### What to build:
Define the strongly-typed `WorkflowClassifier` protocol that establishes the architectural seam between the workflow graph and decision logic. Create an in-memory `FakeClassifierAdapter` capable of returning preconfigured `RouteDecision` and `EvidenceGrade` instances for hermetic testing.

### Acceptance Criteria:
- [x] `WorkflowClassifier` is defined as a runtime-checkable `typing.Protocol` in `app/rag/classifiers.py`.
- [x] Protocol declares synchronous methods:
  - `route(question: str, config: RunnableConfig | None = None) -> RouteDecision`
  - `grade_kb(question: str, current_query: str, kb_docs: list[Document], config: RunnableConfig | None = None) -> EvidenceGrade`
  - `grade_web(question: str, current_query: str, web_results: str, config: RunnableConfig | None = None) -> EvidenceGrade`
- [x] `FakeClassifierAdapter` implements `WorkflowClassifier` with configurable return values for `route`, `grade_kb`, and `grade_web`.
- [x] Unit tests verify that `FakeClassifierAdapter` satisfies `isinstance(adapter, WorkflowClassifier)`.

---

## Ticket 02: Implement `OpenAIClassifierAdapter` & `JevClassifierAdapter`

- **Title:** Implement Production Classifier Adapters (`OpenAIClassifierAdapter` & `JevClassifierAdapter`)
- **Target Files:**
  - `app/rag/classifiers.py`
  - `tests/test_workflow_classifiers.py`
- **Blockers:** Ticket 01
- **Status:** `completed`

### What to build:
Extract and encapsulate the classifier implementations from `fix_single_llm.py` and `fix_single_jev.py` into dedicated adapter classes satisfying `WorkflowClassifier`. Ensure all TypeSafe JEV character token estimation (`_estimate_jev_tokens`) and custom Langfuse span creation (`_record_jev_generation`) are isolated inside `JevClassifierAdapter`.

### Acceptance Criteria:
- [x] `OpenAIClassifierAdapter` implements `WorkflowClassifier` using `ChatOpenAI(temperature=0, stream_usage=True)` with `.with_structured_output(..., method="json_mode")`.
- [x] `OpenAIClassifierAdapter` binds prompts `"rag:router:llm"`, `"rag:grade_kb:llm"`, and `"rag:grade_web:llm"` with Langfuse metadata linking via `_build_prompt_config`.
- [x] `JevClassifierAdapter` implements `WorkflowClassifier` using `TypeSafeClassifier`.
- [x] `JevClassifierAdapter` encapsulates `_estimate_jev_tokens` and custom Langfuse generation span recording without leaking token heuristics to callers.
- [x] Unit tests with mocked LLM/JEV backends verify that both adapters produce valid `RouteDecision` and `EvidenceGrade` objects.

---

## Ticket 03: Consolidate Deep `Workflow Engine` with Pluggable Retriever Hook

- **Title:** Consolidate Deep `Workflow Engine` with Pluggable Retriever Hook
- **Target Files:**
  - `app/rag/engine.py`
  - `tests/test_workflow_engine.py`
- **Blockers:** Ticket 01
- **Status:** `completed`

### What to build:
Implement `app/rag/engine.py` holding the single canonical 10-node LangGraph topology. Support constructor injection of `WorkflowClassifier` and an optional `retriever` (defaulting to `get_retriever()`). Generation nodes (`generate_from_kb`, `generate_from_web`, `direct_answer`) remain `async def` to support streaming tokens. Expose `ask()` and `create_agentic_rag_graph()`.

### Acceptance Criteria:
- [x] `create_agentic_rag_graph(classifier: WorkflowClassifier, retriever: Any = None)` builds and compiles the canonical 10-node StateGraph:
  `route_question` -> `retrieve_kb` -> `grade_kb` -> `search_web` -> `grade_web` -> `rewrite_query` -> `generate_from_kb` / `generate_from_web` / `direct_answer` / `insufficient`.
- [x] Retriever defaults to `app.rag.vectorstore.get_retriever()` when `retriever is None`.
- [x] Nodes bind active step names into `structlog.contextvars.bind_contextvars(step="...")`.
- [x] Unified `ask()` interface accepts `question`, `workflow`, `session_id`, `user_id`, `metadata`, and `callback`, returning standard result dict including `retrieved_contexts`, `citations`, `source_used`, and `trace`.
- [x] Tests verify all graph paths (direct answer, KB retrieval, web fallback, query rewriting, insufficient evidence) using `FakeClassifierAdapter` in memory.

---

## Ticket 04: Wire Backward-Compatibility Shims & Verify Full Regression Suite

- **Title:** Wire Backward-Compatibility Shims & Verify Full Regression Suite
- **Target Files:**
  - `app/rag/fix_single_llm.py`
  - `app/rag/fix_single_jev.py`
  - `tests/test_e2e_observability.py`
  - Existing test suite (260 tests)
- **Blockers:** Ticket 02, Ticket 03
- **Status:** `completed`

### What to build:
Convert `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` into thin shims that wire the unified engine with `OpenAIClassifierAdapter` and `JevClassifierAdapter` respectively. Verify that all 260 existing tests pass with zero modification.

### Acceptance Criteria:
- [x] `app/rag/fix_single_llm.py` is reduced to a thin shim exporting `agent_graph = create_agentic_rag_graph(OpenAIClassifierAdapter())` and `ask(...)`.
- [x] `app/rag/fix_single_jev.py` is reduced to a thin shim exporting `agent_graph = create_agentic_rag_graph(JevClassifierAdapter())` and `ask(...)`.
- [x] `app/api/routes.py` runs without changes: `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` resolve identically.
- [x] Streaming SSE endpoints (`/api/chat/stream`) emit step and token events as expected.
- [x] All 260 existing unit, integration, and streaming tests pass green (`uv run pytest`).
