# ADR 0011: Comprehensive Codebase Unification, Dead-Code Pruning, and Test Suite Modernization

## Status
Accepted (Extends ADR 0006, ADR 0007, ADR 0008, ADR 0009, and ADR 0010)

## Context
Across previous architectural milestones (ADR-0006 through ADR-0010), the system transitioned from hardcoded standalone scripts to normalized protocol seams:
- Workflows unified behind the `WorkflowDefinition` protocol and managed by `WorkflowRegistry`.
- Tools standardized behind LangChain's `BaseTool` with dual outputs (`content_and_artifact`).
- Vector storage and document ingestion abstracted into `VectorStoreAdapter` and `DocumentIndexer`.
- Runtime execution and streaming orchestration extracted into `WorkflowRunner`.

However, to maintain 100% backward compatibility during those incremental transitions, substantial architectural debt was retained:
1. **Legacy Monkeypatch Dictionaries**: `app/rag/workflows/legacy.py` spanned 423 lines hosting `_LegacyAsksDict`, `_LegacyGraphsDict`, and `_LegacyUnifiedWorkflowAdapter` purely to support historical unit tests that patched module dictionaries (`routes.WORKFLOW_ASKS`, `routes.WORKFLOW_GRAPHS`).
2. **Vestigial Workflow Shims**: Standalone modules `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` duplicated graph compilation, bypassed the registry, and forced `app/rag/workflows/__init__.py` to reflect into `sys.modules` to pick up potential test patches.
3. **Engine Globals and Duplicate Sync Logic**: `app/rag/engine.py` retained mutable module globals (`_llm`, `web_search_tool`), standalone synchronous node wrappers (`retrieve_kb`, `search_web`), the legacy `ask()` facade, and deprecated arguments (`retriever`).
4. **Tool Reflection Hacks**: `app/rag/tools/search.py` actively inspected `sys.modules.get("app.rag.engine")` at runtime to check if tests had monkeypatched `engine.web_search_tool`.
5. **Facade Passthrough Modules**: `app/rag/vectorstore.py` and `app/services/ingestion.py` served exclusively as pass-through wrappers around `app.rag.knowledge` components.
6. **Test Suite Debt & Anti-Patterns**: Multiple test files maintained brittle monkeypatches on legacy dictionaries, asserted deprecated AST structures, or validated obsolete shims (`test_legacy_compat.py`, `test_indexing_regression.py`).

To achieve a clean, maintainable, and unified architecture, all backward-compatibility shims must be purged, and the entire test suite modernized to target first-class architectural seams using standard Pytest fixtures and dependency injection.

---

## Decisions

### 1. Complete Removal of Legacy Workflow Dictionaries and Shims
- Permanently delete `app/rag/workflows/legacy.py`.
- Remove all imports and re-exports of `WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, and `reset_legacy_defaults` from `app/api/routes.py` and `app/rag/workflows/__init__.py`.
- Permanently delete standalone workflow shims `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`.
- Eliminate `sys.modules` reflection in `app/rag/workflows/__init__.py:register_default_workflows()`. Workflows are registered strictly through clean instances of `SelfCorrectiveRagWorkflow` and `ReActAgentWorkflow`.
- Modernize external script callers (`evaluation/run_experiment.py`) to execute workflows dynamically through `WorkflowRunner` and `WorkflowRegistry`.

### 2. Pure StateGraph Factory in `app/rag/engine.py`
- Prune `app/rag/engine.py` to act strictly as a factory for StateGraph topologies:
  ```python
  def create_agentic_rag_graph(
      classifier: WorkflowClassifier,
      *,
      model: BaseChatModel | None = None,
      kb_tool: BaseTool | None = None,
      web_tool: BaseTool | None = None,
  ) -> Any
  ```
- Remove module-level global variables (`_llm`, `web_search_tool`).
- Provide a clean, stateless chat model factory `get_chat_model() -> BaseChatModel`.
- Inject `model`, `kb_tool`, and `web_tool` directly into node closures (`node_retrieve_kb`, `node_search_web`, `rewrite_query`, `generate_from_kb`, `generate_from_web`, `direct_answer`).
- Remove redundant standalone synchronous functions (`retrieve_kb`, `search_web`), the legacy facade `engine.ask()`, and the deprecated `retriever` parameter.

### 3. Deepening `SelfCorrectiveRagWorkflow` Seam
- Refactor `SelfCorrectiveRagWorkflow.__init__`:
  - Drop the obsolete `retriever: Any = None` parameter.
  - Explicitly accept `model: BaseChatModel | None = None`, `kb_tool: BaseTool | None = None`, and `web_tool: BaseTool | None = None`, forwarding them directly to `create_agentic_rag_graph()`.

### 4. Canonical Single-Source Ingestion & Facade Removal
- Permanently delete facade modules `app/rag/vectorstore.py` and `app/services/ingestion.py`.
- Define and export `SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md", ".docx"})` directly from `app.rag.knowledge` (and `app.rag.knowledge.parsers`).
- Update `app/api/routes.py` and all callers to import exclusively from `app.rag.knowledge`.

### 5. Elimination of Module Reflection in Tool Implementations
- Remove `sys.modules.get("app.rag.engine")` reflection from `app/rag/tools/search.py:create_web_search_tool()`.
- Tools resolve clients either from explicit constructor dependency injection (`search_client=...`) or from default client providers.

### 6. WorkflowRunner Agnostic Resolution
- Simplify `WorkflowRunner._resolve_workflow(workflow_name)`:
  - Attempt lookup in `self.registry.get(workflow_name)`.
  - If the registry is unpopulated, trigger `register_default_workflows(self.registry)`.
  - Remove hardcoded string checks (`("fix_single_llm", "fix_single_jev", "react_agent")`), preserving the open-closed extensibility principle.

### 7. Modernized Test Suite and Isolation Architecture
- Introduce root `tests/conftest.py` providing an autouse `clean_registry` fixture that resets `WorkflowRegistry` to canonical defaults before and after each test run.
- Provide standardized test mock helpers (`register_mock_workflow`) in `tests/conftest.py` for route, streaming, and feedback tests.
- Permanently delete legacy compatibility test files: `tests/rag/test_legacy_compat.py` and `tests/test_indexing_regression.py`.
- Refactor tests across `test_chat_stream.py`, `test_frontend_streaming.py`, `test_e2e_observability.py`, `test_feedback_api.py`, `test_online_evaluation.py`, `test_logging.py`, and `test_frontend_integration.py` to register mock `WorkflowDefinition`s into `WorkflowRegistry` instead of monkeypatching legacy dictionaries.
- Prune obsolete AST-parsing tests and vestigial sync-node tests from `test_retrieved_contexts.py`, `test_retriever_search_spans.py`, `test_llm_telemetry.py`, and `test_jev_telemetry.py`, while retaining 100% of substantive behavioral assertions (token heuristics, callback propagation, context extraction).

---

## Consequences

### Positive
- **Zero Architectural Debt**: Completely eliminates ~1,000 lines of dead code, compatibility wrappers, AST inspections, and legacy dictionary shims.
- **Strict Modularity & Inversion of Control**: Every subsystem (`app/rag/engine.py`, `SelfCorrectiveRagWorkflow`, `WorkflowRunner`) relies strictly on clean dependency injection without global state or reflection.
- **Robust Test Isolation**: Tests no longer pollute global module dictionaries or rely on fragile monkeypatching; each test operates against an isolated or cleanly reset `WorkflowRegistry`.
- **Open-Closed Extensibility**: New workflows, tools, and parsers can be added without modifying upstream dictionaries or resolution white-lists.

### Negative / Trade-Offs
- **Breaking API Changes for Legacy Callers**: Legacy scripts or third-party tests attempting to import `app.rag.fix_single_llm`, `app.rag.vectorstore`, or monkeypatch `routes.WORKFLOW_ASKS` will fail immediately. All callers must use canonical public seams (`WorkflowRegistry`, `WorkflowRunner`, `app.rag.knowledge`).