# 04: Wire Backward-Compatibility Shims & Verify Full Regression Suite

**What to build:** Convert `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` into thin shims that wire the unified engine with `OpenAIClassifierAdapter` and `JevClassifierAdapter` respectively. Verify that all 260 existing tests pass with zero modification.

**Blocked by:** 02: Implement Production Classifier Adapters (OpenAI & JEV), 03: Consolidate Deep Workflow Engine with Pluggable Retriever Hook

**Status:** ready-for-agent

- [ ] `app/rag/fix_single_llm.py` is reduced to a thin shim exporting `agent_graph = create_agentic_rag_graph(OpenAIClassifierAdapter())` and `ask(...)`.
- [ ] `app/rag/fix_single_jev.py` is reduced to a thin shim exporting `agent_graph = create_agentic_rag_graph(JevClassifierAdapter())` and `ask(...)`.
- [ ] `app/api/routes.py` runs without changes: `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` resolve identically.
- [ ] Streaming SSE endpoints (`/api/chat/stream`) emit step and token events as expected.
- [ ] All 260 existing unit, integration, and streaming tests pass green (`uv run pytest`).
