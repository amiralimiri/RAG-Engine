# 02: Web Search Tool Dual-Output Artifact & Exception Propagation

**What to build:**
Upgrade `create_web_search_tool` in `app/rag/tools/search.py` to adhere to the dual-output `response_format="content_and_artifact"` contract. The tool must return aggregated search prose as `content`, and a strongly-typed dictionary `{"citations": list[dict[str, Any]], "retrieved_contexts": list[str]}` as `artifact`. Both sync `_run` and async `_arun` must be supported, and provider exceptions must bubble up.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Red: Add test in `tests/rag/test_tools.py` asserting `create_web_search_tool().invoke({"query": "..."})` returns `(content, artifact)` where `artifact["citations"]` is `list[dict]` and `artifact["retrieved_contexts"]` is `list[str]`.
- [ ] Red: Add test in `tests/rag/test_tools.py` asserting `await create_web_search_tool().ainvoke({"query": "..."})` returns the same dual-output structure.
- [ ] Red: Add test asserting provider exceptions (e.g. Tavily API rate limit `HTTPError`) bubble up directly from `invoke` and `ainvoke`.
- [ ] Green: Refactor `_run` and `_arun` in `app/rag/tools/search.py` to return `(content, {"citations": citations, "retrieved_contexts": contexts})`.
- [ ] Refactor: Ensure clean typing and docstrings, verifying all tests in `tests/rag/test_tools.py` pass.
