# 04: Engine Pruning & Legacy Entrypoint Modernization

**What to build:** Refactor `app/rag/engine.py` to remove mutable module globals and vestigial synchronous logic, retaining it strictly as a StateGraph compiler while routing `engine.ask()` and legacy shims (`fix_single_llm`, `fix_single_jev`) through `WorkflowRegistry` and `WorkflowRunner`.

**Blocked by:** 01: WorkflowRunner Core Execution & Unit Test Suite, 03: Legacy Compatibility Facade & Test-Shim Isolation

**Status:** completed

- [x] Delete `_COMPILED_GRAPHS` and `_get_workflow_graph()` from `app/rag/engine.py`.
- [x] Refactor `app.rag.engine.ask()` to delegate directly to `get_workflow_runner().execute()` via a thin facade preserving its signature and argument defaults.
- [x] Refactor `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` to obtain compiled graphs from `WorkflowRegistry.get(wf_id).get_compiled_graph()` and execute via `WorkflowRunner`.
- [x] Refactor top-level standalone `retrieve_kb` and `search_web` in `engine.py` to delegate internally to `create_retriever_tool(retriever=retriever).invoke` and `create_web_search_tool().invoke`.
- [x] Remove `_web_search` and `_default_web_search_tool` globals from `engine.py`.
- [x] Run and verify `tests/test_workflow_engine.py`, `tests/test_retriever_search_spans.py`, and `tests/test_retrieved_contexts.py` pass with zero regressions.
