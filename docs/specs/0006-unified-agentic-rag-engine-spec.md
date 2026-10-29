# Technical Specification: Unified Agentic RAG Workflow Engine & Classifier Seam

- **ADR Reference:** `docs/adr/0006-unified-agentic-rag-engine-and-classifier-seam.md`
- **Specification Document:** `docs/specs/0006-unified-agentic-rag-engine-spec.md`
- **Target Version:** `0.6.0`
- **Status:** Completed

---

## Problem Statement

The enterprise IT support RAG system maintains two separate LangGraph workflows (`fix_single_llm` and `fix_single_jev`) in isolated files (`app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`). Over 85% of the codebase across these two files is identical: they share the exact same 10-node graph topology, state schema, prompt compilation mechanics, web search integration, citations synthesis, and error fallbacks. 

This duplication creates acute architectural friction:
- Every graph change (such as converting generation nodes to asynchronous invocation for SSE token streaming, or updating telemetry bindings) must be implemented and verified in two places, risking logic drift and divergence.
- Classifier-specific implementation details—such as TypeSafe JEV heuristic character token estimation and custom Langfuse span creation—leak directly into graph execution nodes rather than being encapsulated.
- The absence of a formal seam between the workflow topology and decision classifiers prevents future modularity, such as independently swapping vector stores or introducing new routing topologies.
- API routes in `app/api/routes.py` must manage dual graph dictionaries and branch on workflow names to construct metadata.

---

## Solution

Consolidate the duplicated workflow backends into a single, deep **Workflow Engine** (`app/rag/engine.py`) backed by an explicit **Workflow Classifier** seam (`app/rag/classifiers.py`). 

The new architecture:
1. Establishes a strongly-typed `WorkflowClassifier` protocol defining discrete synchronous methods for `route`, `grade_kb`, and `grade_web`.
2. Implements two pluggable adapters: `OpenAIClassifierAdapter` (structured JSON outputs) and `JevClassifierAdapter` (TypeSafe Classifier with fully encapsulated token accounting and Langfuse observation creation).
3. Consolidates the complete LangGraph topology into `app/rag/engine.py` with an adapter-injected factory `create_agentic_rag_graph(classifier, retriever=None)` and a unified execution interface `ask()`.
4. Leaves `fix_single_llm.py` and `fix_single_jev.py` as lightweight backward-compatibility shims (~10 lines each) exporting pre-wired graph instances, ensuring all existing routes, frontend interactions, and 260 tests pass without modification.
5. Adds an optional `retriever` parameter to the graph compiler, cleanly paving the way for Candidate 4 (swappable vector knowledge stores).

---

## User Stories

1. As an enterprise IT support user, I want the assistant to answer questions using private knowledge, web fallback, or direct responses identically regardless of which classifier is active, so that my support experience remains consistent.
2. As a frontend client developer, I want `GET /api/workflows`, `POST /api/chat`, and `POST /api/chat/stream` to continue supporting `"fix_single_llm"` and `"fix_single_jev"`, so that UI selectors and chat streams require zero client-side modifications.
3. As a backend engineer, I want the 10-step LangGraph topology defined in exactly one place, so that improvements to query rewriting, search, or generation automatically apply across all workflow backends.
4. As a backend engineer, I want classifier-specific logic (e.g. TypeSafe JEV custom span recording and token heuristics) encapsulated inside its adapter, so that LangGraph execution nodes contain zero vendor-specific branching.
5. As an LLMOps engineer, I want request traces in Langfuse to retain their existing trace names, metadata, and span hierarchies for both `fix_single_llm` and `fix_single_jev`, so that historical telemetry dashboards and cost analytics remain unbroken.
6. As a QA engineer, I want existing test suites (`test_chat_stream.py`, `test_frontend_streaming.py`, `test_jev_telemetry.py`, `test_llm_telemetry.py`) to continue passing without signature breaks, so that regression risks are eliminated.
7. As a backend engineer, I want to test the entire LangGraph workflow in memory using a lightweight fake classifier adapter, so that graph flow tests can run in milliseconds without network calls or API keys.
8. As a backend engineer, I want `create_agentic_rag_graph` to accept an optional retriever argument, so that future work can inject an in-memory knowledge store adapter without breaking graph construction.
9. As an evaluation runner, I want `evaluation/run_experiment.py` to benchmark both `fix_single_llm` and `fix_single_jev` through the same unified engine interface, so that comparative accuracy metrics reflect only classifier performance differences.
10. As a maintenance engineer, I want prompt compilation and template fallback logic consolidated into shared helpers, so that prompt updates in the Langfuse Prompt CMS bind reliably across all nodes.
11. As a systems architect, I want `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` reduced to thin declarative shims, so that deletion and deprecation paths are straightforward in the future.
12. As an observability engineer, I want active LangGraph execution steps (`retrieve_kb`, `grade_kb`, `search_web`, etc.) bound to `structlog.contextvars` in one consolidated place, so that structured logs maintain complete context propagation.

---

## Implementation Decisions

### 1. Classifier Seam Protocol (`app/rag/classifiers.py`)
- Define `WorkflowClassifier` as a `typing.Protocol` with runtime checkability:
  ```python
  class WorkflowClassifier(Protocol):
      def route(
          self,
          question: str,
          config: RunnableConfig | None = None,
      ) -> RouteDecision: ...

      def grade_kb(
          self,
          question: str,
          current_query: str,
          kb_docs: list[Document],
          config: RunnableConfig | None = None,
      ) -> EvidenceGrade: ...

      def grade_web(
          self,
          question: str,
          current_query: str,
          web_results: str,
          config: RunnableConfig | None = None,
      ) -> EvidenceGrade: ...
  ```
- All protocol methods are synchronous (`def`), matching underlying SDK calls and avoiding superfluous event loop hops.
- Return types strictly adhere to existing Pydantic models from `app.rag.state` (`RouteDecision` with `.route`, `EvidenceGrade` with `.grade`).

### 2. Concrete Classifier Adapters (`app/rag/classifiers.py`)
- **`OpenAIClassifierAdapter`**:
  - Lazily initializes `ChatOpenAI(temperature=0, stream_usage=True)` via configured model settings.
  - Compiles prompts using `get_workflow_prompt("rag:router:llm")`, `get_workflow_prompt("rag:grade_kb:llm")`, and `get_workflow_prompt("rag:grade_web:llm")`.
  - Uses `with_structured_output(..., method="json_mode")`.
  - Merges prompt observation metadata via `_build_prompt_config`.
- **`JevClassifierAdapter`**:
  - Lazily initializes `TypeSafeClassifier`.
  - Compiles prompts using `rag:router:jev`, `rag:grade_kb:jev`, and `rag:grade_web:jev`.
  - Evaluates decisions using `Choice` criteria.
  - Fully absorbs `_estimate_jev_tokens()` and `_record_jev_generation()`, publishing custom generation spans and token costs to Langfuse directly within the adapter.

### 3. Deep Workflow Engine (`app/rag/engine.py`)
- Consolidates the complete 10-node StateGraph topology:
  - `route_question` (invokes `classifier.route`)
  - `retrieve_kb` (invokes `retriever.invoke`)
  - `grade_kb` (invokes `classifier.grade_kb`)
  - `search_web` (invokes `web_search_tool`)
  - `grade_web` (invokes `classifier.grade_web`)
  - `rewrite_query` (invokes `llm().ainvoke`)
  - `generate_from_kb` (async def, invokes `llm().ainvoke`)
  - `generate_from_web` (async def, invokes `llm().ainvoke`)
  - `direct_answer` (async def, invokes `llm().ainvoke`)
  - `insufficient` (static terminal node)
- Generation nodes remain `async def` to guarantee streaming token propagation (`on_chat_model_stream`) in `astream_events(version="v2")`.
- Exposes:
  - `create_agentic_rag_graph(classifier: WorkflowClassifier, retriever: Any = None) -> Any`
  - `ask(question: str, workflow: str = "fix_single_llm", session_id: str | None = None, user_id: str | None = None, metadata: dict[str, Any] | None = None, callback: Any = None) -> dict[str, Any]`

### 4. Backward-Compatibility Shims
- `app/rag/fix_single_llm.py`:
  - Instantiates `OpenAIClassifierAdapter()`.
  - Builds `agent_graph = create_agentic_rag_graph(OpenAIClassifierAdapter())`.
  - Re-exports `ask()` pre-bound to `workflow="fix_single_llm"`.
- `app/rag/fix_single_jev.py`:
  - Instantiates `JevClassifierAdapter()`.
  - Builds `agent_graph = create_agentic_rag_graph(JevClassifierAdapter())`.
  - Re-exports `ask()` pre-bound to `workflow="fix_single_jev"`.

### 5. API Routes Alignment (`app/api/routes.py`)
- Routes continue to consume `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` referencing `fix_single_llm` and `fix_single_jev`.
- Telemetry bindings, streaming generators, and error handling remain completely intact.

---

## Testing Decisions

### Seam Strategy
- **Primary Seam**: The `WorkflowClassifier` protocol in `app/rag/classifiers.py`.
  - Tests can substitute a `FakeClassifierAdapter` to verify all graph routing branches (direct answer, KB retrieval, web search fallback, query rewrite loops, insufficient evidence) deterministically without calling OpenAI or TypeSafe APIs.
- **Workflow Seam**: The `ask()` interface and `agent_graph.astream_events()` in `app/rag/engine.py`.
  - Verifies that state transitions, citations formatting, contextvars binding, and token streaming operate identically across both adapter configurations.
- **Compatibility Seam**: The re-exported entry points in `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`.
  - Ensures the existing 260 tests pass with zero modification.

### Good Test Principles
- Test external observable behavior (returned answers, sources used, citation links, trace steps, emitted SSE events) rather than private node closures.
- Ensure telemetry callbacks receive identical observation tags and trace metadata under the refactored engine.

### Prior Art
- `tests/test_chat_stream.py`: Full SSE streaming verification across workflows.
- `tests/test_llm_telemetry.py` & `tests/test_jev_telemetry.py`: Specific callback and cost recording assertions.
- `tests/test_e2e_observability.py`: Complete API-level integration tests.

---

## Out of Scope

- Implementing the pluggable vector store / in-memory store adapter (Candidate 4). This spec only adds the parameter hook (`retriever: Any = None`) to prepare for it.
- Modifying prompt texts in `LOCAL_PROMPTS` or Langfuse Prompt CMS.
- Changing frontend UI components or CSS styles.
- Altering the evaluation engine (`app/services/evaluation.py`, Candidate 2) or ingestion pipeline (`app/services/ingestion.py`, Candidate 3).

---

## Further Notes

- Once this specification is implemented, deleting or adding future workflow backends (e.g. a hypothetical local Ollama classifier) requires only adding one new adapter class without touching graph topology.
- Performance characteristics remain unchanged: node execution latency, streaming time-to-first-token (TTFT), and memory footprint are preserved.
