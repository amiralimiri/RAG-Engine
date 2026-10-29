# 03: Pure Engine Factory & Tool Decoupling

**What to build:** Refactor `app/rag/engine.py` into a pure, stateless StateGraph factory (`create_agentic_rag_graph`) with explicit dependency injection for chat model (`model: BaseChatModel | None = None`) and tools (`kb_tool: BaseTool | None = None`, `web_tool: BaseTool | None = None`), eliminating module globals (`_llm`, `web_search_tool`), vestigial synchronous functions (`retrieve_kb`, `search_web`), `engine.ask()`, and the deprecated `retriever` parameter. Update `SelfCorrectiveRagWorkflow` to pass injected tools and model, remove `sys.modules` reflection in `app/rag/tools/search.py`, and refactor `tests/test_workflow_engine.py`.

**Blocked by:** 01: Test Isolation Fixture Foundation

**Status:** closed

---

## Tasks

- [x] In `app/rag/engine.py`:
  - Implement `get_chat_model() -> BaseChatModel` returning a freshly configured `ChatOpenAI` instance from settings.
  - Remove module-level global variables `_llm = None` and `web_search_tool = None`.
  - Remove standalone synchronous functions `retrieve_kb`, `search_web`, and `ask`.
  - Update `create_agentic_rag_graph`:
    - New signature: `create_agentic_rag_graph(classifier: WorkflowClassifier, *, model: BaseChatModel | None = None, kb_tool: BaseTool | None = None, web_tool: BaseTool | None = None) -> Any`.
    - Drop deprecated `retriever: Any = None` parameter.
    - Resolve `active_model = model if model is not None else get_chat_model()`.
    - Resolve `active_kb_tool = kb_tool if kb_tool is not None else create_retriever_tool()`.
    - Resolve `active_web_tool = web_tool if web_tool is not None else create_web_search_tool()`.
    - Bind `active_model` into the closures of `rewrite_query`, `generate_from_kb`, `generate_from_web`, and `direct_answer`.
    - Update `__all__ = ["GENERATION_NODES", "get_chat_model", "create_agentic_rag_graph"]`.
- [x] In `app/rag/workflows/self_corrective.py`:
  - Update `SelfCorrectiveRagWorkflow.__init__`:
    - New signature: `(id: str, name: str, classifier: WorkflowClassifier, *, model: BaseChatModel | None = None, kb_tool: BaseTool | None = None, web_tool: BaseTool | None = None, description: str | None = None, step_labels: dict[str, str] | None = None, generation_nodes: set[str] | None = None, metadata: dict[str, Any] | None = None)`.
    - Drop `retriever: Any = None` parameter and `self.retriever` attribute.
    - Store `self.model`, `self.kb_tool`, `self.web_tool`.
    - In `get_compiled_graph()`, call `create_agentic_rag_graph(classifier=self.classifier, model=self.model, kb_tool=self.kb_tool, web_tool=self.web_tool)`.
- [x] In `app/rag/tools/search.py`:
  - Remove `sys.modules.get("app.rag.engine")` monkeypatch check from `_get_active_client()`.
  - Rely exclusively on `search_client` (if provided) or `TavilySearch` instance.
- [x] In `tests/test_workflow_engine.py`:
  - Remove tests asserting `engine.ask()` and synchronous `retrieve_kb` / `search_web`.
  - Update graph creation tests to inject mock tools and mock model directly into `create_agentic_rag_graph(classifier, model=mock_llm, kb_tool=mock_kb, web_tool=mock_web)`.
  - Remove monkeypatches targeting `app.rag.engine.llm` and `app.rag.engine.web_search_tool`.

## Verification
- Run `uv run pytest tests/test_workflow_engine.py tests/rag/test_self_corrective_contract.py tests/rag/test_tools.py`.
