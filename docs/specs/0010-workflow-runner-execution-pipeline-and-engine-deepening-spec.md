# Technical Specification: Workflow Runner Execution Pipeline and Engine Deepening

- **ADR Reference:** `docs/adr/0010-workflow-runner-execution-pipeline-and-engine-deepening.md`
- **Target Version:** `0.10.0`
- **Status:** Proposed

---

## 1. Problem Statement

Following ADR 0006, ADR 0007, ADR 0008, and ADR 0009, the agentic RAG platform established a unified 10-node StateGraph engine, pluggable classifier adapters, an extensible multi-workflow registry, a dual-output `BaseTool` layer, and an independent vector store seam.

However, architectural friction and shallowness remain prominent along two execution seams:
1. **Transport Layer Bloat**: `app/api/routes.py` has grown to 745 lines by absorbing LangGraph runtime orchestration: manually consuming `astream_events` v2, suppressing tool-call token deltas (`is_tool_call_chunk`), mapping step labels, formatting SSE event payloads (`event: step`, `event: token`, `event: metadata`, `event: error`), managing structlog `contextvars` bindings, capturing client disconnect cancellations, managing background evaluation task sets, and hosting legacy test-shim dictionary adapters (`_LegacyUnifiedWorkflowAdapter`, `_LegacyAsksDict`, `_LegacyGraphsDict`, `WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`).
2. **Lingering Engine Globals and Vestigial Nodes**: `app/rag/engine.py` retains redundant module-level globals (`_COMPILED_GRAPHS`, `_get_workflow_graph()`) and standalone synchronous node functions (`retrieve_kb`, `search_web`) that duplicate the lifecycle already handled by `WorkflowRegistry` and `BaseTool`. Furthermore, legacy shims (`fix_single_llm.py`, `fix_single_jev.py`) bypass the registry to call private engine internals directly.
3. **Execution Inconsistency Across Transports**: Executing a workflow synchronously through `chat()`, streaming it through `chat_stream()`, or testing it requires duplicating callback setup, trace ID resolution, context binding, and fallback handling.

To achieve strict modularity, the HTTP router must serve purely as a lightweight transport adapter, while workflow execution, token streaming, telemetry context propagation, and background evaluation scheduling concentrate inside a dedicated, deep `WorkflowRunner` module.

---

## 2. Solution

Establish a unified execution runtime subsystem in `app/rag/workflows/runner.py` that encapsulates all graph execution, SSE streaming, token delta filtering, and background evaluation behind a minimal 2-method interface.

1. **Dedicated `WorkflowRunner` Class (`app/rag/workflows/runner.py`)**:
   - Encapsulates workflow resolution from `WorkflowRegistry`, Langfuse callback creation, structlog context variable binding, LangGraph execution, fallback handling, structured logging, and background evaluation scheduling.
   - Exposes two high-leverage entrypoints:
     - `execute(request: ChatRequest, background_tasks: BackgroundTasks | None = None) -> ChatResponse`
     - `stream(request: ChatRequest) -> AsyncGenerator[str, None]`
   - Provides an injectable singleton factory `get_workflow_runner(registry=None)`.

2. **Thin Transport Router (`app/api/routes.py`)**:
   - Refactor `chat` and `chat_stream` endpoints to delegate directly to `WorkflowRunner.execute()` and `StreamingResponse(WorkflowRunner.stream(), media_type="text/event-stream")`.
   - Remove manual SSE formatting, event filtering, and context variable binding from `app/api/routes.py`, reducing its size from 745 lines to under 120 lines.

3. **Isolated Legacy Test-Shim Adapter (`app/rag/workflows/legacy.py`)**:
   - Move `_LegacyUnifiedWorkflowAdapter`, `_sync_legacy_registry`, `_LegacyAsksDict`, and `_LegacyGraphsDict` out of `app/api/routes.py` into `app/rag/workflows/legacy.py`.
   - Re-export `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` in `app/api/routes.py` strictly as backward-compatible facade properties, ensuring zero regression for existing monkeypatched test suites.

4. **Pruning Engine Globals & Consolidating `app/rag/engine.py`**:
   - Remove `_COMPILED_GRAPHS` and private caching routines from `app/rag/engine.py`. Retain `engine.py` strictly as a factory for StateGraph topologies (`create_agentic_rag_graph`).
   - Retain `app.rag.engine.ask()` strictly as a backward-compatible facade delegating directly to `get_workflow_runner().execute()`.
   - Update legacy shims (`app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`) to resolve workflows through `WorkflowRegistry.get()` and execute via `WorkflowRunner`.
   - Refactor standalone synchronous `retrieve_kb` and `search_web` in `engine.py` to delegate internally to `create_retriever_tool()` and `create_web_search_tool()`, preserving backward compatibility for unit tests while eliminating duplicate citation parsing and Tavily client instantiation.

5. **Protected Background Online Evaluation Scheduling**:
   - In `WorkflowRunner.execute()`, schedule `run_online_evaluation_task` via FastAPI's `BackgroundTasks` if provided, falling back to `asyncio.create_task` if invoked outside an HTTP background context.
   - In `WorkflowRunner.stream()`, manage background evaluation tasks via a guarded, module-level `set[asyncio.Task]` with completion callbacks, guaranteeing evaluation tasks are not prematurely garbage-collected during streaming responses.

---

## 3. User Stories

1. **As an API client**, I want `POST /api/chat` and `POST /api/chat/stream` to invoke workflows through a standardized runner, so that responses, streaming tokens, step updates, and errors are handled identically across all topologies.
2. **As a frontend developer**, I want SSE stream events (`step`, `token`, `metadata`, `error`) to adhere strictly to the established data contracts with zero leakage of internal tool-call JSON deltas.
3. **As a test engineer**, I want to invoke `WorkflowRunner.execute()` or `WorkflowRunner.stream()` directly in automated tests without needing a FastAPI `TestClient` or HTTP loopback.
4. **As a platform architect**, I want `app/api/routes.py` to be a pure transport adapter under 120 lines, so that transport logic is completely decoupled from workflow execution.
5. **As a workflow author**, I want to register a new workflow in `WorkflowRegistry` and have it instantly executable and streamable over HTTP without touching a single line of router code.
6. **As an observability engineer**, I want telemetry context variables (`workflow`, `trace_id`, `session_id`, `user_id`) to be bound consistently in both sync and streaming executions and flushed upon completion.
7. **As an evaluation engineer**, I want online DeepEval evaluation tasks to trigger reliably in the background during both sync and streaming runs without being prematurely garbage-collected on client disconnects.
8. **As a maintainer**, I want existing test suites that monkeypatch `routes.WORKFLOW_ASKS` or `routes.WORKFLOW_GRAPHS` to continue functioning without modification via `app/rag/workflows/legacy.py`.
9. **As a maintainer**, I want existing tests calling `engine.ask()` or inspecting `engine.retrieve_kb` / `engine.search_web` signatures to continue passing with 100% backward compatibility.
10. **As a core developer**, I want `app/rag/engine.py` to be free of mutable module globals (`_COMPILED_GRAPHS`) so that compiled graph lifecycle and state are strictly managed by `WorkflowRegistry`.

---

## 4. Implementation Decisions & Component Specifications

### 4.1. WorkflowRunner Architecture (`app/rag/workflows/runner.py`)

#### 4.1.1. Class Structure & Signatures
```python
"""WorkflowRunner deep execution and streaming pipeline module."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from fastapi import BackgroundTasks
from pydantic import BaseModel
import structlog

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    StreamErrorEvent,
    StreamMetadataEvent,
    StreamStepEvent,
    StreamTokenEvent,
)
from app.rag.workflows.base import WorkflowDefinition
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry

logger = structlog.get_logger(__name__)


class WorkflowRunner:
    """Deep module coordinating workflow execution, token streaming, telemetry, and evaluation."""

    def __init__(self, registry: WorkflowRegistry | None = None) -> None:
        self.registry = registry or get_workflow_registry()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def execute(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
    ) -> ChatResponse:
        """Synchronously execute a registered workflow and return ChatResponse."""
        ...

    async def aexecute(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
    ) -> ChatResponse:
        """Asynchronously execute a registered workflow and return ChatResponse."""
        ...

    async def stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[str, None]:
        """Consume LangGraph astream_events v2 and yield pre-formatted SSE event strings."""
        ...

    def format_sse(self, event: str, data: dict[str, Any] | BaseModel) -> str:
        """Format an event frame into SSE string protocol."""
        ...

    def is_tool_call_chunk(self, chunk: Any) -> bool:
        """Filter out token deltas when chunk contains tool call chunks or structured calls."""
        ...
```

#### 4.1.2. Singleton Factory
```python
_default_runner: WorkflowRunner | None = None


def get_workflow_runner(registry: WorkflowRegistry | None = None) -> WorkflowRunner:
    """Return the global default WorkflowRunner singleton or a customized instance."""
    global _default_runner
    if registry is not None:
        return WorkflowRunner(registry=registry)
    if _default_runner is None:
        _default_runner = WorkflowRunner()
    return _default_runner
```

---

### 4.2. SSE Stream Serialization & Error Propagation Contract

#### 4.2.1. SSE Event Types and Ordering
1. `event: step`: Emitted when `on_chain_start` occurs for a node listed in `workflow_def.info.step_labels`.
   ```
   event: step
   data: {"step": "route_question", "label": "Analyzing inquiry & routing...", "timestamp": 1729000000.1}
   ```
2. `event: token`: Emitted when `on_chat_model_stream` fires for a node listed in `workflow_def.info.generation_nodes`, provided `is_tool_call_chunk(chunk)` is `False`.
   ```
   event: token
   data: {"delta": "Hello"}
   ```
3. `event: metadata`: Emitted upon normal completion of the graph with finalized trace, citations, source used, and rewritten query.
   ```
   event: metadata
   data: {"trace_id": "...", "workflow": "fix_single_llm", "source_used": "vectorstore", "citations": [...], "trace": [...], "rewritten_query": "..."}
   ```
4. `event: error`: Emitted if an unhandled exception escapes graph execution.
   ```
   event: error
   data: {"message": "Inference stream failed: ...", "code": 500}
   ```

#### 4.2.2. Error Handling & Graceful Fallback
- When an exception occurs during `execute()` or `stream()`, `WorkflowRunner` delegates to `workflow_def.handle_error(exc, state)` to synthesize a fallback answer and preserve execution traces.
- In `stream()`, client disconnects (`asyncio.CancelledError`, `GeneratorExit`) log structured cancellation telemetry with `source_used="client_disconnected"` and re-raise to cleanly abort the HTTP connection.
- Telemetry callbacks (`CallbackHandler.flush()`) and `flush_telemetry()` are guaranteed to execute in a `finally` block.

---

### 4.3. Legacy Compatibility Seam (`app/rag/workflows/legacy.py`)

To eliminate 160 lines of monkeypatch adaptation logic from `app/api/routes.py`, `app/rag/workflows/legacy.py` encapsulates:
1. `_LegacyUnifiedWorkflowAdapter`: Wraps legacy callable `ask_fn` or runnable `graph` into a conforming `WorkflowDefinition`.
2. `_LegacyAsksDict` / `_LegacyGraphsDict`: Custom `dict` subclasses that synchronize monkeypatched values with `WorkflowRegistry`.
3. `_sync_legacy_registry(workflow_id: str)`: Sync helper registering dynamic shims.
4. Exported instances:
   ```python
   WORKFLOW_ASKS = _LegacyAsksDict(...)
   WORKFLOW_GRAPHS = _LegacyGraphsDict(...)
   ```

`app/api/routes.py` re-exports `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` from `app.rag.workflows.legacy` for 100% backward compatibility with test suites that patch `routes.WORKFLOW_ASKS`.

---

### 4.4. Refactoring `app/rag/engine.py`

1. **Delete Module-Level State**:
   - Remove `_COMPILED_GRAPHS`.
   - Remove private `_get_workflow_graph(workflow: str)`.
2. **Pure StateGraph Factory**:
   - `create_agentic_rag_graph(...)` remains the primary exported StateGraph compiler.
3. **Engine `ask()` Facade**:
   - `ask(question, workflow="fix_single_llm", ...)` delegates to `get_workflow_runner().execute(request)`.
4. **Synchronous Node Shims**:
   - Refactor `retrieve_kb(state, config, retriever)` to delegate to `create_retriever_tool(retriever=retriever).invoke(...)`.
   - Refactor `search_web(state, config)` to delegate to `create_web_search_tool().invoke(...)`.
   - Remove `_web_search` global and `_default_web_search_tool`.

---

### 4.5. Thin HTTP Router (`app/api/routes.py`)

The refactored router reduces to:
```python
@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, background_tasks: BackgroundTasks):
    runner = get_workflow_runner()
    return runner.execute(payload, background_tasks=background_tasks)


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    runner = get_workflow_runner()
    return StreamingResponse(
        runner.stream(payload),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

---

## 5. Testing Decisions & Backward Compatibility Guarantees

### 5.1. Testing Principles
- Tests must verify external behavior through public module interfaces, avoiding private attribute introspection.
- `WorkflowRunner` tests will verify both `execute()` and `stream()` against mock and registered workflows without HTTP transport dependencies.
- Conformance tests verify that legacy monkeypatched dictionaries (`routes.WORKFLOW_ASKS`) update the underlying registry correctly.

### 5.2. New and Modified Test Suites
1. **`tests/rag/test_workflow_runner.py`** (New):
   - Unit tests for `WorkflowRunner.execute()` with synchronous graphs and error fallbacks.
   - Unit tests for `WorkflowRunner.stream()` verifying step events, token delta filtering, tool-call suppression, metadata emission, and error events.
   - Test cancellation handling on client disconnect.
   - Test online evaluation task scheduling.
2. **`tests/api/test_routes_multi_workflow.py`**:
   - Verify `POST /api/chat` and `POST /api/chat/stream` work seamlessly through the new runner.
3. **`tests/test_workflow_engine.py`**:
   - Verify `engine.ask()` facade passes all arguments and metadata to `WorkflowRunner`.
   - Verify `engine.retrieve_kb` and `engine.search_web` retain signatures and return expected dictionary schemas.
4. **Existing Regression Suite (438 tests)**:
   - Must pass completely with 0 regressions.

---

## 6. Out of Scope

- Merging `app/services/evaluation.py` and `evaluation/run_experiment.py` (Candidate 2; deferred to subsequent ADR).
- Deepening prompt fetching into a unified `PromptRunner` (Candidate 4; deferred to subsequent ADR).
- Altering the 10-node StateGraph topology or classifier decisions.
- Introducing persistent Redis/Postgres checkpointers for conversational workflows.

---

## 7. Further Notes

- **Locality Win**: Modifying streaming event formats, tool-chunk filters, or telemetry context injection now requires editing only `app/rag/workflows/runner.py`.
- **Leverage Win**: Future transport protocols (e.g., WebSockets, gRPC, CLI) can invoke `WorkflowRunner.execute()` and `WorkflowRunner.stream()` directly with identical guarantees.
