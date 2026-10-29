# 03: Consolidate Deep Workflow Engine with Pluggable Retriever Hook

**What to build:** Implement `app/rag/engine.py` holding the canonical 10-node StateGraph topology. Support constructor injection of `WorkflowClassifier` and optional `retriever` (defaulting to `get_retriever()`). Generation nodes remain `async def` for streaming. Expose `ask()` and `create_agentic_rag_graph()`.

**Blocked by:** 01: Define WorkflowClassifier Protocol Seam and Test Double

**Status:** completed

- [x] `create_agentic_rag_graph(classifier: WorkflowClassifier, retriever: Any = None)` builds and compiles the canonical 10-node StateGraph.
- [x] Retriever defaults to `app.rag.vectorstore.get_retriever()` when `retriever is None`.
- [x] Nodes bind active step names into `structlog.contextvars.bind_contextvars(step="...")`.
- [x] Unified `ask()` interface accepts `question`, `workflow`, `session_id`, `user_id`, `metadata`, and `callback`, returning standard result dict including `retrieved_contexts`, `citations`, `source_used`, and `trace`.
- [x] Unit tests in `tests/test_workflow_engine.py` verify all graph branches in memory using `FakeClassifierAdapter`.
