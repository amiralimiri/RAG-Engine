# 06: Telemetry, Contexts & Spans Test Suite Consolidation

**What to build:** Modernize and consolidate tests in `test_retrieved_contexts.py`, `test_retriever_search_spans.py`, `test_llm_telemetry.py`, and `test_jev_telemetry.py`. Retain 100% of substantive behavioral assertions (token heuristics, cost calculation, retriever/search tool callbacks, context propagation) by targeting `WorkflowRunner` and `BaseTool` seams, while purging obsolete AST checks and synchronous engine node assertions.

**Blocked by:** 01: Test Isolation Fixture Foundation, 03: Pure Engine Factory & Tool Decoupling, 04: Legacy Workflow Shims & Dictionary Deletion

**Status:** closed

---

## Tasks

- [x] In `tests/test_retrieved_contexts.py`:
  - Remove imports of deleted modules (`fix_single_llm`, `fix_single_jev`).
  - Remove direct calls to deleted synchronous functions (`engine.retrieve_kb`, `engine.search_web`).
  - Test context population on the graph nodes or via `create_agentic_rag_graph` and `WorkflowRunner`.
  - Ensure assertions verifying `AgentState.retrieved_contexts`, `generate_from_kb`, `generate_from_web`, and `insufficient` pass using the pure factory.
- [x] In `tests/test_retriever_search_spans.py`:
  - Delete `test_strict_zero_coupling_between_llm_and_jev` (which parsed AST of deleted files).
  - Update `test_fix_single_llm_retrieve_kb_*` and `test_fix_single_llm_search_web_*` to test `create_retriever_tool()` and `create_web_search_tool()` directly, asserting callback firing (`on_retriever_start`, `on_retriever_end`, `on_tool_start`, `on_tool_end`) and config forwarding without calling deleted module functions in `engine.py`.
- [x] In `tests/test_llm_telemetry.py`:
  - Remove `from app.rag import fix_single_llm`.
  - Refactor tests to verify telemetry kwargs (`session_id`, `user_id`, `metadata`, `tags`, `callback`) forwarding via `WorkflowRunner.execute_with_state(ChatRequest(question="hello", workflow="fix_single_llm", ...))` or by invoking the workflow definition directly.
- [x] In `tests/test_jev_telemetry.py`:
  - Retain `test_estimate_jev_tokens` (validates heuristic token counting).
  - Remove `from app.rag import fix_single_jev`.
  - Refactor `test_ask_jev_*` tests to invoke `WorkflowRunner.execute_with_state(ChatRequest(question="hello", workflow="fix_single_jev", ...))` with mock `JevClassifierAdapter` choices.

## Verification
- Run `uv run pytest tests/test_retrieved_contexts.py tests/test_retriever_search_spans.py tests/test_llm_telemetry.py tests/test_jev_telemetry.py`.
