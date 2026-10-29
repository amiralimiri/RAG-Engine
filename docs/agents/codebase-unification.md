# Codebase Unification & Zero-Shim Architecture

Guidelines and patterns established in ADR-0011 (Comprehensive Codebase Unification, Dead-Code Pruning, and Test Suite Modernization) for maintaining a minimal, unified codebase free of legacy compatibility shims, mutable module globals, and monkeypatched dictionaries.

---

## 1. Core Principles

1. **Zero Backward-Compatibility Shims in Domain & Transport**:
   - Never create or retain backward-compatibility wrappers, pass-through facades, or legacy dictionary adapters (`WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, `_LegacyUnifiedWorkflowAdapter`).
   - If a caller requires a service, workflow, or tool, interact directly with the first-class architectural seam (`WorkflowRegistry`, `WorkflowRunner`, `VectorStoreAdapter`, `DocumentIndexer`, `BaseTool`).

2. **Explicit Dependency Injection (No Mutable Module Globals)**:
   - Module-level globals (such as `_llm = None` or `web_search_tool = None`) are strictly prohibited in workflow factories.
   - Inject model instances (`model: BaseChatModel | None = None`) and tool instances (`kb_tool: BaseTool | None = None`, `web_tool: BaseTool | None = None`) into factory parameters (e.g. `create_agentic_rag_graph()`), binding them directly into node closures.

3. **No Dynamic Reflection into `sys.modules`**:
   - Code must never inspect `sys.modules` to detect if an upstream file, module global, or test mock was attached at runtime.
   - Configure dependencies via constructor arguments or explicit protocol registrations.

4. **Pure Transport Adapters**:
   - HTTP routes in `app/api/routes.py` must remain lightweight protocol adapters (< 120 lines) delegating directly to `WorkflowRunner` and `DocumentIndexer`.
   - Never re-export domain dictionaries or legacy aliases on the API router.

5. **Canonical Ingestion Exports**:
   - All file parsing and supported format definitions live in `app.rag.knowledge`.
   - `SUPPORTED_EXTENSIONS` is exported directly from `app.rag.knowledge`. Facade modules like `app/services/ingestion.py` and `app/rag/vectorstore.py` are permanently retired.

---

## 2. Test Suite Standards & Fixture Architecture

1. **Centralized Test Isolation via `tests/conftest.py`**:
   - An autouse `clean_registry` fixture resets `WorkflowRegistry` before and after every test, ensuring zero inter-test leakage.
   - Do not write manual teardown logic in individual test files.

2. **Standardized Mock Registration**:
   - Never monkeypatch module dictionaries.
   - When mocking a workflow or graph execution, register a lightweight `WorkflowDefinition` double directly into `WorkflowRegistry`:
     ```python
     registry.register(mock_workflow_def, override=True)
     ```
   - Alternatively, inject mock hooks into `WorkflowRunner` or mock dependencies at the seam boundary.

3. **Purge Compatibility Assertions**:
   - Do not write tests that exist solely to assert backward-compatibility shims, AST structure of deleted files, or obsolete synchronous functions.
   - Focus unit and contract tests on domain contracts, protocol conformance, token accounting, and streaming correctness.

4. **Modern Typing Standards**:
   - Universal adherence to PEP 604 union types (`T | None = None`) across all application domain modules, test doubles, and utility scripts. Avoid legacy `typing.Optional`.
