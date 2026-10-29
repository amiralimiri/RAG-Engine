# 01: Retriever Tool Dual-Output Artifact & Exception Propagation

**What to build:**
Upgrade `create_retriever_tool` in `app/rag/tools/retriever.py` to adhere to the dual-output `response_format="content_and_artifact"` contract. The tool must return human-readable document prose as `content`, and a strongly-typed dictionary `{"documents": list[Document], "citations": list[dict[str, Any]]}` as `artifact`. Both sync `_run` and async `_arun` must be supported, and provider exceptions must propagate transparently.

**Blocked by:** None (can start immediately)

**Status:** closed

- [x] Red: Add test in `tests/rag/test_tools.py` asserting `create_retriever_tool().invoke({"query": "..."})` returns `(content, artifact)` where `artifact["documents"]` is `list[Document]` and `artifact["citations"]` is `list[dict]`.
- [x] Red: Add test in `tests/rag/test_tools.py` asserting `await create_retriever_tool().ainvoke({"query": "..."})` returns the same dual-output structure.
- [x] Red: Add test asserting provider exceptions (e.g. `ConnectionError`) bubble up directly from `invoke` and `ainvoke`.
- [x] Green: Refactor `_run` and `_arun` in `app/rag/tools/retriever.py` to return `(content, {"documents": docs, "citations": citations})`.
- [x] Refactor: Ensure clean typing and docstrings, verifying all tests in `tests/rag/test_tools.py` pass.
