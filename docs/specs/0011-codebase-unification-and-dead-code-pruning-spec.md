# Technical Specification: Codebase Unification, Dead-Code Pruning, and Test Suite Modernization

- **ADR Reference:** `docs/adr/0011-codebase-unification-and-dead-code-pruning.md`
- **Target Version:** `0.11.0`
- **Status:** Proposed

---

## 1. Problem Statement

Across prior architectural transitions (ADR-0006 through ADR-0010), the repository adopted first-class protocols:
- `WorkflowDefinition` and `WorkflowRegistry` for workflow lifecycle management.
- Dual-output `BaseTool` (`content_and_artifact`) for knowledge base and web search tools.
- `VectorStoreAdapter` and `DocumentIndexer` under `app.rag.knowledge` for CQRS storage and ingestion.
- `WorkflowRunner` for unified execution, SSE streaming, and telemetry orchestration.

However, to prevent breaks across older tests during those incremental migrations, significant architectural debt and accidental complexity were intentionally left in place:
1. **Legacy Dictionary Monkeypatching**: `app/rag/workflows/legacy.py` spanned 423 lines hosting `_LegacyAsksDict`, `_LegacyGraphsDict`, and `_LegacyUnifiedWorkflowAdapter` purely to allow unit tests to execute `monkeypatch.setitem(WORKFLOW_ASKS, ...)` and `monkeypatch.setitem(WORKFLOW_GRAPHS, ...)`.
2. **Vestigial Standalone Shims**: `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` existed solely as historical entrypoints exposing `ask()` and `agent_graph`, forcing `app/rag/workflows/__init__.py` to inspect `sys.modules` for monkeypatched graphs.
3. **Engine Globals & Duplicate Synchronous Logic**: `app/rag/engine.py` retained mutable globals (`_llm`, `web_search_tool`), standalone synchronous wrappers (`retrieve_kb`, `search_web`), the legacy `ask()` facade, and deprecated arguments (`retriever: Any = None`).
4. **Tool Reflection Hacks**: `app/rag/tools/search.py` inspected `sys.modules.get("app.rag.engine")` at runtime to detect monkeypatches on `engine.web_search_tool`.
5. **Facade Passthrough Modules**: `app/rag/vectorstore.py` and `app/services/ingestion.py` served exclusively as pass-through shims to `app.rag.knowledge`.
6. **Test Suite Debt & Brittle Tests**: Dedicated test files asserted obsolete compatibility shims (`test_legacy_compat.py`, `test_indexing_regression.py`), while other tests parsed ASTs of deleted files or monkeypatched legacy route dictionaries across 8+ test modules.

To achieve a unified, maintainable, and high-performance architecture, all dead code, compatibility shims, and reflection hacks must be eradicated, and the test suite modernized around standard Pytest fixtures and explicit dependency injection.

---

## 2. Solution & Goals

1. **Purge All Compatibility Shims**:
   - Delete `app/rag/workflows/legacy.py`, `app/rag/fix_single_llm.py`, `app/rag/fix_single_jev.py`, `app/rag/vectorstore.py`, and `app/services/ingestion.py`.
   - Remove re-exports of `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` from `app/api/routes.py` and `app/rag/workflows/__init__.py`.
2. **Pure StateGraph Factory in `app/rag/engine.py`**:
   - Prune `engine.py` down to a pure factory (`create_agentic_rag_graph`) with explicit injection of `model: BaseChatModel | None`, `kb_tool: BaseTool | None`, and `web_tool: BaseTool | None`.
   - Remove `_llm`, `web_search_tool`, `retrieve_kb`, `search_web`, `engine.ask()`, and `retriever`.
3. **Decouple Tool Implementations**:
   - Remove `sys.modules` reflection from `app/rag/tools/search.py`.
4. **Canonical Single-Source File Formats**:
   - Export `SUPPORTED_EXTENSIONS: frozenset[str]` directly from `app.rag.knowledge`.
5. **Agnostic Workflow Resolution**:
   - Simplify `WorkflowRunner._resolve_workflow` to resolve from `WorkflowRegistry` without hardcoded ID tuples.
6. **Centralized Test Isolation & Modernized Fixtures**:
   - Establish `tests/conftest.py` with an autouse `clean_registry` fixture and a standardized `register_mock_workflow` test helper.
   - Refactor all tests that previously monkeypatched `WORKFLOW_ASKS` / `WORKFLOW_GRAPHS` to register mock workflows into `clean_registry`.
   - Delete `test_legacy_compat.py` and `test_indexing_regression.py`.
   - Prune obsolete AST-parsing and sync-node checks from remaining telemetry and context tests while retaining 100% of substantive behavioral assertions.
7. **External Script Modernization**:
   - Update `evaluation/run_experiment.py` to invoke workflows through `WorkflowRunner` and `WorkflowRegistry`.

---

## 3. User Stories

1. **As a core developer**, I want `app/rag/engine.py` to be a pure StateGraph factory without module globals, so that topology construction is completely deterministic and testable via dependency injection.
2. **As a workflow author**, I want `WorkflowRegistry` to be the sole authority for workflow lifecycles without hidden `sys.modules` reflection or monkeypatched dictionary overrides.
3. **As a tool author**, I want `create_web_search_tool()` to rely strictly on constructor parameters and configuration settings, without inspecting `sys.modules["app.rag.engine"]`.
4. **As an API maintainer**, I want `app/api/routes.py` to import `SUPPORTED_EXTENSIONS` directly from `app.rag.knowledge`, eliminating legacy pass-through modules.
5. **As a test engineer**, I want an autouse `clean_registry` fixture in `tests/conftest.py` that guarantees pristine workflow registration before and after every test, preventing cross-test pollution.
6. **As an evaluation engineer**, I want `evaluation/run_experiment.py` to dynamically benchmark any workflow in `WorkflowRegistry` via `WorkflowRunner`, without hardcoding legacy `ask` imports.
7. **As a platform architect**, I want all legacy shims (`legacy.py`, `fix_single_*.py`, `vectorstore.py`, `ingestion.py`) removed, ensuring that every file in the repository conforms strictly to current standards.

---

## 4. File Deletions and Updated Import Paths

### 4.1. File Deletions

| File Path | Reason for Deletion |
|---|---|
| `app/rag/workflows/legacy.py` | 423 lines of legacy dictionary shims (`WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, `_LegacyUnifiedWorkflowAdapter`) |
| `app/rag/fix_single_llm.py` | Historical standalone workflow shim exposing `ask()` and `agent_graph` |
| `app/rag/fix_single_jev.py` | Historical standalone workflow shim exposing `ask()` and `agent_graph` |
| `app/rag/vectorstore.py` | Historical pass-through facade wrapping `app.rag.knowledge` |
| `app/services/ingestion.py` | Historical pass-through facade wrapping `CompositeDocumentParser` and `RecursiveCharacterChunker` |
| `tests/rag/test_legacy_compat.py` | Unit tests asserting the deleted `legacy.py` dictionary adapters |
| `tests/test_indexing_regression.py` | Unit tests asserting the deleted `vectorstore.py` and `ingestion.py` facades |

### 4.2. Updated Import Paths

| Old / Deprecated Import | New Canonical Import |
|---|---|
| `from app.rag.workflows.legacy import WORKFLOW_ASKS, WORKFLOW_GRAPHS` | **Removed**: tests register mock workflows via `clean_registry.register(...)` |
| `from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS` | **Removed**: routes no longer re-export dictionaries |
| `from app.services.ingestion import SUPPORTED, load_file, chunk_documents` | `from app.rag.knowledge import SUPPORTED_EXTENSIONS, CompositeDocumentParser, RecursiveCharacterChunker` |
| `from app.rag.vectorstore import get_vectorstore, get_retriever, add_documents` | `from app.rag.knowledge import get_knowledge_store` |
| `from app.rag.fix_single_llm import ask, agent_graph` | `WorkflowRegistry.get("fix_single_llm")` + `WorkflowRunner.execute(...)` |
| `from app.rag.fix_single_jev import ask, agent_graph` | `WorkflowRegistry.get("fix_single_jev")` + `WorkflowRunner.execute(...)` |
| `from app.rag.engine import ask, retrieve_kb, search_web, web_search_tool` | `get_workflow_runner().execute(...)` and `create_agentic_rag_graph(..., kb_tool=..., web_tool=...)` |

---

## 5. Architectural Seam Specifications

### 5.1. Pure StateGraph Factory (`app/rag/engine.py`)

#### 5.1.1. Model Factory
```python
def get_chat_model() -> BaseChatModel:
    """Instantiate a stateless ChatOpenAI model configured with application settings."""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
        stream_usage=True,
    )
```

#### 5.1.2. Graph Factory Signature
```python
def create_agentic_rag_graph(
    classifier: WorkflowClassifier,
    *,
    model: BaseChatModel | None = None,
    kb_tool: BaseTool | None = None,
    web_tool: BaseTool | None = None,
) -> Any:
    """Build and compile the canonical 10-node StateGraph topology.

    Args:
        classifier: The WorkflowClassifier adapter driving routing and evidence grading.
        model: Optional BaseChatModel instance for query rewrite and answer generation.
            Defaults to get_chat_model().
        kb_tool: Optional BaseTool instance for knowledge base retrieval.
            Defaults to create_retriever_tool().
        web_tool: Optional BaseTool instance for web search fallback.
            Defaults to create_web_search_tool().

    Returns:
        Compiled StateGraph runnable wrapped with wrap_safe_invoke ready for execution.
    """
```

#### 5.1.3. Node Closure Dependency Binding
Inside `create_agentic_rag_graph`:
- `active_model = model if model is not None else get_chat_model()`
- `active_kb_tool = kb_tool if kb_tool is not None else create_retriever_tool()`
- `active_web_tool = web_tool if web_tool is not None else create_web_search_tool()`
- Nodes `rewrite_query`, `generate_from_kb`, `generate_from_web`, and `direct_answer` invoke `active_model.ainvoke(prompt, config=run_cfg)` directly from closure scope.
- Nodes `node_retrieve_kb` and `node_search_web` invoke `active_kb_tool.ainvoke(...)` and `active_web_tool.ainvoke(...)` directly.
- The module-level globals `_llm` and `web_search_tool`, synchronous node functions `retrieve_kb` and `search_web`, and `ask()` are completely removed.

### 5.2. `SelfCorrectiveRagWorkflow` Seam (`app/rag/workflows/self_corrective.py`)

```python
class SelfCorrectiveRagWorkflow:
    """WorkflowDefinition implementation for the 10-node self-corrective RAG DAG."""

    def __init__(
        self,
        id: str,
        name: str,
        classifier: WorkflowClassifier,
        *,
        model: BaseChatModel | None = None,
        kb_tool: BaseTool | None = None,
        web_tool: BaseTool | None = None,
        description: str | None = None,
        step_labels: dict[str, str] | None = None,
        generation_nodes: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.classifier = classifier
        self.model = model
        self.kb_tool = kb_tool
        self.web_tool = web_tool
        # ... WorkflowInfo initialization ...

    def get_compiled_graph(self) -> Any:
        with self._lock:
            if self._compiled_graph is None:
                self._compiled_graph = create_agentic_rag_graph(
                    classifier=self.classifier,
                    model=self.model,
                    kb_tool=self.kb_tool,
                    web_tool=self.web_tool,
                )
            return self._compiled_graph
```

### 5.3. Agnostic Workflow Resolution (`app/rag/workflows/runner.py`)

```python
def _resolve_workflow(self, workflow_name: str) -> WorkflowDefinition:
    """Resolve workflow definition from registry, auto-registering defaults if empty."""
    if len(self.registry) == 0:
        from app.rag.workflows import register_default_workflows
        register_default_workflows(self.registry)

    try:
        return self.registry.get(workflow_name)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown workflow: {workflow_name}")
```

### 5.4. Decoupled Tool Factory (`app/rag/tools/search.py`)

In `create_web_search_tool(search_client: Any = None, ...)`:
- Remove `sys.modules.get("app.rag.engine")` reflection.
- `_get_active_client()`:
  - If `search_client is not None`: use `search_client` (or `search_client()` if callable).
  - Otherwise, instantiate `TavilySearch` using configured `tavily_api_key`.

### 5.5. Canonical Ingestion Extensions (`app/rag/knowledge/`)

In `app/rag/knowledge/parsers.py`:
```python
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md", ".docx"})
```
Export `SUPPORTED_EXTENSIONS` from `app.rag.knowledge.__init__.py`. In `app/api/routes.py`:
```python
from app.rag.knowledge import SUPPORTED_EXTENSIONS, get_document_indexer
```

---

## 6. Centralized Test Isolation & Modernized Fixtures (`tests/conftest.py`)

### 6.1. Root Fixtures Architecture

```python
"""Root Pytest configuration and shared test isolation fixtures."""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import MagicMock
import pytest

from app.api.schemas import ChatRequest, ChatResponse
from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry
from app.rag.workflows import register_default_workflows


class MockWorkflowDefinition:
    """Lightweight configurable WorkflowDefinition for route and integration testing."""

    def __init__(
        self,
        id: str,
        name: str = "Mock Workflow",
        *,
        answer: str = "Mock response",
        source_used: str = "knowledge_base",
        trace: list[str] | None = None,
        citations: list[dict[str, Any]] | None = None,
        retrieved_contexts: list[str] | None = None,
        stream_events: list[dict[str, Any]] | None = None,
        graph: Any = None,
    ) -> None:
        self.info = WorkflowInfo(
            id=id,
            name=name,
            description=f"Mock workflow definition for {id}",
            step_labels={"retrieve_kb": "Searching internal knowledge base..."},
            generation_nodes={"generate_from_kb"},
        )
        self.answer = answer
        self.source_used = source_used
        self.trace = trace or ["step1", "step2"]
        self.citations = citations or []
        self.retrieved_contexts = retrieved_contexts or []
        self._custom_graph = graph
        self._stream_events = stream_events

    def get_compiled_graph(self) -> Any:
        if self._custom_graph is not None:
            return self._custom_graph

        mock = MagicMock()
        mock.invoke.return_value = {
            "answer": self.answer,
            "source_used": self.source_used,
            "trace": self.trace,
            "citations": self.citations,
            "retrieved_contexts": self.retrieved_contexts,
        }

        async def _astream_events(state, version="v2", **kwargs):
            if self._stream_events:
                for evt in self._stream_events:
                    yield evt
            else:
                yield {
                    "event": "on_chat_model_stream",
                    "metadata": {"langgraph_node": "generate_from_kb"},
                    "data": {"chunk": MagicMock(content=self.answer, tool_call_chunks=[])},
                }

        mock.astream_events = _astream_events
        return mock

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        return {
            "question": request.question,
            "trace_id": trace_id or request.trace_id,
            "current_query": request.question,
        }

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        return ChatResponse(
            workflow=self.info.id,
            answer=final_state.get("answer") or self.answer,
            source_used=final_state.get("source_used") or self.source_used,
            trace=final_state.get("trace") or self.trace,
            citations=final_state.get("citations") or self.citations,
            trace_id=trace_id or final_state.get("trace_id"),
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used") or self.source_used,
            retrieved_contexts=final_state.get("retrieved_contexts") or self.retrieved_contexts,
        )

    def handle_error(self, exc: Exception, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": f"Inference error: {exc}",
            "source_used": "direct",
            "trace": ["error_fallback"],
            "retrieved_contexts": [],
            "citations": [],
        }


@pytest.fixture(autouse=True)
def clean_registry():
    """Autouse fixture resetting WorkflowRegistry to canonical defaults before and after each test."""
    registry = get_workflow_registry()
    registry.clear()
    register_default_workflows(registry)
    yield registry
    registry.clear()
    register_default_workflows(registry)


@pytest.fixture
def register_mock_workflow(clean_registry: WorkflowRegistry) -> Callable[..., MockWorkflowDefinition]:
    """Helper fixture to create and register a MockWorkflowDefinition into clean_registry."""
    def _register(workflow_id: str, **kwargs: Any) -> MockWorkflowDefinition:
        wf = MockWorkflowDefinition(id=workflow_id, **kwargs)
        clean_registry.register(wf, override=True)
        return wf
    return _register
```

---

## 7. Step-by-Step Test Migration Plan

### 7.1. Files to Delete
- `tests/rag/test_legacy_compat.py`: Delete entirely.
- `tests/test_indexing_regression.py`: Delete entirely.

### 7.2. Files to Refactor

| Test File | Required Changes |
|---|---|
| `tests/test_chat_stream.py` | Replace all `monkeypatch.setitem(WORKFLOW_GRAPHS, ...)` and `monkeypatch.setitem(WORKFLOW_ASKS, ...)` with `clean_registry.register(MockWorkflowDefinition(...), override=True)`. Remove `from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS`. |
| `tests/test_frontend_streaming.py` | Replace `WORKFLOW_ASKS` / `WORKFLOW_GRAPHS` monkeypatching with mock workflow registration. Remove imports from `routes`. |
| `tests/test_e2e_observability.py` | Replace `WORKFLOW_ASKS` / `WORKFLOW_GRAPHS` monkeypatching with mock workflow registration or dependency injection. |
| `tests/test_feedback_api.py` | Replace `monkeypatch.setitem(WORKFLOW_ASKS, ...)` with mock workflow registration. |
| `tests/test_online_evaluation.py` | Replace `WORKFLOW_ASKS` / `WORKFLOW_GRAPHS` monkeypatches with mock workflow registration. |
| `tests/unit/test_logging.py` | Update test at line 366 (`test_logging_middleware_workflow_asks`) to register a mock workflow in `clean_registry` instead of patching `routes.WORKFLOW_ASKS`. |
| `tests/test_frontend_integration.py` | Replace `monkeypatch.setitem(WORKFLOW_ASKS, ...)` with mock workflow registration. |
| `tests/test_workflow_engine.py` | Remove tests targeting `engine.ask()` and sync `retrieve_kb` / `search_web`. Update graph creation tests to inject mock tools via `kb_tool` and `web_tool` and mock model via `model`. |
| `tests/test_retrieved_contexts.py` | Remove calls to deleted synchronous functions `engine.retrieve_kb()` and `engine.search_web()`. Test context population on the graph topology via `create_agentic_rag_graph` or `WorkflowRunner`. |
| `tests/test_retriever_search_spans.py` | Delete `test_strict_zero_coupling_between_llm_and_jev` (which parsed AST of deleted files). Refactor retriever and tool callback tests to assert `create_retriever_tool()` and `create_web_search_tool()`. |
| `tests/test_llm_telemetry.py` | Refactor from testing deleted `fix_single_llm.ask()` to testing `WorkflowRunner` execution with the `fix_single_llm` workflow definition. |
| `tests/test_jev_telemetry.py` | Retain `test_estimate_jev_tokens` and token cost assertions; refactor `test_ask_jev_*` tests to invoke `WorkflowRunner.execute` with `fix_single_jev`. |
| `tests/api/test_routes_upload.py` | Update assertions to ensure `SUPPORTED_EXTENSIONS` is respected. |

### 7.3. External Script Modernization
- `evaluation/run_experiment.py`:
  - Remove `from app.rag import fix_single_jev, fix_single_llm`.
  - Execute workflows via `get_workflow_runner().execute_with_state(ChatRequest(question=q, workflow=wf_name))` and `wf_def.extract_eval_context(state)`.
  - Support benchmarking any workflow present in `WorkflowRegistry`.

---

## 8. Verification & Acceptance Criteria

1. **Compilation & Import Integrity**:
   - Zero references to deleted files (`legacy.py`, `fix_single_llm.py`, `fix_single_jev.py`, `vectorstore.py`, `ingestion.py`).
   - Zero references to `WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, `_LegacyUnifiedWorkflowAdapter`, or `reset_legacy_defaults`.
   - `python -c "import app.main; import app.rag.workflows; import app.rag.knowledge"` executes with 0 warnings or import errors.
2. **Pure Engine Factory**:
   - `create_agentic_rag_graph` creates executable topologies with explicit `model`, `kb_tool`, and `web_tool` injection.
   - `app/rag/engine.py` contains 0 module-level mutable variables (`_llm = None` is gone).
3. **Clean Tool Resolution**:
   - `create_web_search_tool` operates without any reflection into `sys.modules`.
4. **Test Suite Passes 100%**:
   - Running `uv run pytest` executes cleanly with 0 failures, 0 legacy monkeypatch errors, and all obsolete test files removed.
5. **Architectural Conformance**:
   - Direction of dependencies is strictly enforced (transport -> runner -> registry -> workflow/engine/knowledge).
   - No dynamic Pydantic model mutation.
   - All optional parameters adhere strictly to PEP 484 union types (`T | None = None`).
