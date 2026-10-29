# ADR 0006: Unified Agentic RAG Workflow Engine with Pluggable Classifier Seam

## Status
Accepted (Supersedes the standalone workflow preservation clauses in ADR 0001 and ADR 0005)

## Context
In ADR 0001 and ADR 0005, the self-corrective RAG workflows (`fix_single_llm` and `fix_single_jev`) were maintained as strictly isolated, separate files (`app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`) to avoid premature abstraction. 

Over time, this structure created significant architectural friction:
1. **85% Topology Duplication**: The LangGraph state topology (`route_question` -> `retrieve_kb` -> `grade_kb` -> `search_web` -> `grade_web` -> `rewrite_query` -> `generate_from_kb` / `web` / `direct`), citations formatting, prompt configuration, and error fallbacks were duplicated across both files.
2. **Maintenance Drag**: Graph modifications (such as converting generation nodes to async for SSE token streaming, or updating context propagation) had to be manually synchronized across both files, risking divergence.
3. **Leaking Classifier Mechanics**: JEV-specific heuristic token estimation and custom Langfuse span creation leaked into LangGraph execution nodes rather than being isolated behind an adapter.
4. **Impediment to Full Modularity**: Future requirements (such as pluggable knowledge stores and alternate graph topologies) were blocked by the tight coupling between the graph definition and decision logic.

---

## Decisions

### 1. Consolidated Workflow Engine (`app/rag/engine.py`)
- Define a single canonical LangGraph topology in `app/rag/engine.py` that handles all state transitions, node executions, citations formatting, and result synthesis.
- Expose a factory function `create_agentic_rag_graph(classifier: WorkflowClassifier, retriever: Any = None)` that compiles the LangGraph instance by injecting the classifier adapter and retriever at compilation time.
- Expose a unified execution interface `ask(question, workflow="fix_single_llm", ...)` that manages session tracking, telemetry callbacks, and structured logging.

### 2. Pluggable Classifier Seam (`app/rag/classifiers.py`)
- Define a strongly-typed `WorkflowClassifier` protocol with three discrete synchronous domain methods:
  - `route(question: str, config: RunnableConfig | None) -> RouteDecision`
  - `grade_kb(question: str, current_query: str, kb_docs: list[Document], config: RunnableConfig | None) -> EvidenceGrade`
  - `grade_web(question: str, current_query: str, web_results: str, config: RunnableConfig | None) -> EvidenceGrade`
- Implement two concrete adapters satisfying the seam:
  - `OpenAIClassifierAdapter`: Uses ChatOpenAI structured outputs (`json_mode` with Pydantic models).
  - `JevClassifierAdapter`: Uses TypeSafe Classifier (`typesafe/jev-1.13`), fully encapsulating custom token estimation and Langfuse span creation.

### 3. Preparation for Knowledge Store Seam
- Accept an optional `retriever` parameter in `create_agentic_rag_graph`, defaulting to Pinecone's `get_retriever()`. This decouples the retrieval step from the vector database and paves the way for Candidate 4 (pluggable Knowledge Store adapters).

### 4. Backward Compatibility Shims
- `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` are preserved as thin shims that re-export `agent_graph` and `ask` pre-configured with their respective classifier adapters.
- Existing API routes (`/api/chat`, `/api/chat/stream`), frontend selectors, and test suites continue to invoke `"fix_single_llm"` and `"fix_single_jev"` without breaking changes.

---

## Consequences
- **Locality**: LangGraph node transitions, citations, and streaming token logic concentrate in one module (`app/rag/engine.py`).
- **Leverage**: All workflow backends share identical topology improvements, telemetry propagation, and bug fixes automatically.
- **Seam Isolation**: JEV-specific token accounting is completely hidden behind `JevClassifierAdapter`.
- **Testability**: Graph execution can be verified independently with fast in-memory fake classifier adapters, while classifier adapters can be verified through focused unit tests.
