# ADR 0010: Workflow Runner Execution Pipeline and Engine Deepening

## Status
Accepted (Extends ADR 0006, ADR 0007, and ADR 0008)

## Context
Following ADR 0006 (Unified Agentic RAG Engine) and ADR 0007 (Multi-Workflow Extensibility Platform), workflows were successfully normalized behind the `WorkflowDefinition` protocol and managed centrally by `WorkflowRegistry`. 

However, architectural friction emerged along two seams:
1. **Transport Layer Bloat**: `app/api/routes.py` grew to 745 lines by absorbing LangGraph runtime orchestration: manually consuming `astream_events` v2, suppressing tool-call token deltas (`is_tool_call_chunk`), mapping step labels, formatting SSE event payloads, managing structlog `contextvars` bindings, capturing client disconnect cancellations, managing background evaluation task sets, and hosting legacy test-shim dictionary adapters (`_LegacyUnifiedWorkflowAdapter`, `WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`).
2. **Lingering Engine Globals and Vestigial Nodes**: `app/rag/engine.py` retained redundant module-level globals (`_COMPILED_GRAPHS`, `_get_workflow_graph()`) and standalone synchronous node functions (`retrieve_kb`, `search_web`) that duplicated the lifecycle already handled by `WorkflowRegistry` and `BaseTool`. Furthermore, legacy shims (`fix_single_llm.py`, `fix_single_jev.py`) bypassed the registry to call private engine internals directly.

To enforce strict modularity, the HTTP transport layer must serve exclusively as a thin protocol adapter, while workflow execution, token streaming, and runtime telemetry concentrate in a dedicated, deep module.

---

## Decisions

### 1. Dedicated `WorkflowRunner` Execution Seam (`app/rag/workflows/runner.py`)
- Extract all runtime execution and streaming orchestration out of `app/api/routes.py` into a deep `WorkflowRunner` module with an injectable `WorkflowRegistry` dependency.
- Expose a compact 2-method interface:
  - `execute(request: ChatRequest, background_tasks: BackgroundTasks | None = None) -> ChatResponse`: Coordinates workflow resolution, telemetry context propagation, LangGraph execution, fallback handling, structured logging, and background evaluation scheduling.
  - `stream(request: ChatRequest) -> AsyncGenerator[str, None]`: Consumes LangGraph `astream_events` v2, filters out tool-call token chunks, yields pre-formatted SSE event frames (`step`, `token`, `metadata`, `error`), flushes telemetry, and safely schedules background evaluation tasks across client cancellations.
- Provide a module-level singleton factory `get_workflow_runner()` defaulting to `get_workflow_registry()`.

### 2. Router As Pure Transport Adapter (`app/api/routes.py`)
- Refactor `chat` and `chat_stream` endpoints in `app/api/routes.py` into concise handlers (< 120 lines total file size) that delegate directly to `WorkflowRunner.execute()` and `StreamingResponse(WorkflowRunner.stream(), media_type="text/event-stream")`.
- Remove manual SSE formatting, event filtering, and context variable binding from the HTTP routing tier.

### 3. Isolation of Legacy Test-Shim Adapters (`app/rag/workflows/legacy.py`)
- Move `_LegacyUnifiedWorkflowAdapter`, `_sync_legacy_registry`, `_LegacyAsksDict`, and `_LegacyGraphsDict` out of `app/api/routes.py` into `app/rag/workflows/legacy.py`.
- Re-export `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` in `app/api/routes.py` strictly as backward-compatible facade properties, ensuring zero regression for existing monkeypatched test suites.

### 4. Pruning Engine Singletons & Consolidating `app/rag/engine.py`
- Remove `_COMPILED_GRAPHS` and private caching routines from `app/rag/engine.py`. Retain `engine.py` strictly as a factory for StateGraph topologies (`create_agentic_rag_graph`).
- Retain `app.rag.engine.ask()` strictly as a backward-compatible facade delegating directly to `get_workflow_runner().execute()`.
- Update legacy shims (`app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`) to resolve workflows through `WorkflowRegistry.get()` and execute via `WorkflowRunner`.
- Refactor standalone synchronous `retrieve_kb` and `search_web` in `engine.py` to delegate internally to `create_retriever_tool()` and `create_web_search_tool()`, preserving backward compatibility for unit tests while eliminating duplicate citation parsing and Tavily client instantiation.

### 5. Protected Background Online Evaluation Management
- In `WorkflowRunner.execute()`, schedule `run_online_evaluation_task` via FastAPI's `BackgroundTasks` if provided, falling back to `asyncio.create_task` if invoked outside an HTTP background context.
- In `WorkflowRunner.stream()`, manage background evaluation tasks via a guarded, module-level `set[asyncio.Task]` with completion callbacks, guaranteeing evaluation tasks are not prematurely garbage-collected during streaming responses.

---

## Consequences

### Positive
- **Locality**: SSE framing, tool-call token delta filtering, and execution telemetry concentrate inside `app/rag/workflows/runner.py`. The HTTP router shrinks by ~600 lines.
- **Leverage**: The exact same `WorkflowRunner` interface powers HTTP endpoints, automated integration tests, and future transport interfaces (WebSocket, CLI, gRPC) without HTTP client overhead.
- **Seam Cleanliness**: `app/rag/engine.py` is freed from mutable global caches, while `WorkflowRegistry` becomes the single source of truth for workflow lifecycles across the entire codebase.
- **Zero Regressions**: 100% backward compatibility maintained for existing test suites monkeypatching `routes.WORKFLOW_ASKS`, inspecting `engine.retrieve_kb`, or calling `engine.ask()`.

### Negative
- **Indirection**: Callers routing through `app/rag/engine.ask()` or legacy route dictionaries experience one additional layer of delegation to `WorkflowRunner`.
