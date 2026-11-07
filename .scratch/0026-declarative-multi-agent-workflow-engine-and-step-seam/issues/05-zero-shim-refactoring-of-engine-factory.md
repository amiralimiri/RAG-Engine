# 05: Zero-Shim Refactoring of Engine Factory

**What to build:**
Refactoring of `app/rag/engine.py` to replace the 9 nested closure functions inside `create_agentic_rag_graph` with cleanly instantiated `WorkflowStep` classes from `app/rag/steps/`.
Shrinks `create_agentic_rag_graph` from ~250 lines of nested closures to ~35 lines of declarative graph topology wiring.
Preserves the exact external signature of `create_agentic_rag_graph` and all public exports (`GENERATION_NODES`, `get_chat_model`, `route_start`, etc.) with zero breaking changes to existing callers (`SelfCorrectiveRagWorkflow`), workflows (`fix_single_llm`, `fix_single_jev`), or test suites.

**Blocked by:**
03: Concrete Step Implementations

**Status:**
completed

- [x] Delete the 9 nested closure functions inside `create_agentic_rag_graph` in `app/rag/engine.py` (`node_condense_query`, `node_route_question`, `node_retrieve_kb`, `node_grade_kb`, `node_search_web`, `node_grade_web`, `rewrite_query`, `generate_from_kb`, `generate_from_web`, `node_direct_answer`).
- [x] Refactor `create_agentic_rag_graph` to instantiate and add step classes directly:
  - `QueryReformulationStep` for `condense_query` and `rewrite_query`.
  - `ClassifierRouteStep` and `ClassifierGradingStep` for `route_question`, `grade_kb`, and `grade_web`.
  - `ToolRetrievalStep` for `retrieve_kb` and `search_web`.
  - `PromptGenerationStep` for `generate_from_kb`, `generate_from_web`, and `direct_answer`.
- [x] Keep conditional routing functions (`route_start`, `route_after_router`, `after_kb`, `after_web`) intact.
- [x] Run all existing tests in `tests/rag/test_engine.py`, `tests/rag/test_workflows.py`, and `tests/rag/test_react_agent.py` ensuring 100% pass rate with zero regression.
- [x] Pass `ruff check` and `pyright` on `app/rag/engine.py`.
