# 03: Workflow Engine Tool Injection & Async Node Promotion

**What to build:**
Refactor `create_agentic_rag_graph` in `app/rag/engine.py` to accept explicit `kb_tool: BaseTool | None = None` and `web_tool: BaseTool | None = None` keyword arguments, maintaining backward-compatibility for `retriever: Any = None`. Promote `retrieve_kb` and `search_web` to native `async def` nodes that invoke `await tool.ainvoke(...)`, directly unpacking tool artifacts into `AgentState` without redundant parsing. Delete module-level `_web_search`, `web_search_tool()`, and duplicated Tavily/Pinecone parsing routines.

**Blocked by:** 01-retriever-tool-artifact, 02-web-search-tool-artifact

**Status:** ready-for-agent

- [ ] Red: Add test in `tests/test_workflow_engine.py` asserting `create_agentic_rag_graph` accepts `kb_tool` and `web_tool`, invoking them during graph execution.
- [ ] Red: Add test in `tests/test_workflow_engine.py` asserting legacy `retriever=mock_retriever` is automatically adapted to `kb_tool` when `kb_tool` is not provided.
- [ ] Red: Add test in `tests/test_workflow_engine.py` asserting `retrieve_kb` and `search_web` execute asynchronously without blocking during `astream_events` v2.
- [ ] Green: Refactor `create_agentic_rag_graph`, `retrieve_kb`, and `search_web` in `app/rag/engine.py` to use injected `BaseTool` instances via `await tool.ainvoke`.
- [ ] Green: Remove duplicate `_web_search`, `web_search_tool()`, and redundant manual string/citation parsing from `engine.py`.
- [ ] Refactor: Verify all existing tests in `tests/test_workflow_engine.py` pass.
