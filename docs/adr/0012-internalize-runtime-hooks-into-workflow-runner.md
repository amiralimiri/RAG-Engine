# ADR 0012: Internalize Runtime Hooks into WorkflowRunner

## Status
Accepted (Extends ADR 0010 and ADR 0011)

## Context
In ADR-0010, the `WorkflowRunner` module was introduced to centralize LangGraph workflow execution, token delta streaming, telemetry binding, and background online evaluation dispatch. However, to support runtime overrides across transitions, `WorkflowRunner` exposed four optional hook callables (`callback_factory`, `trace_extractor`, `telemetry_flusher`, and `eval_task_fn`) on its constructor and duplicated them across all public execution methods:
- `execute(request, background_tasks, callback_factory=..., trace_extractor=..., telemetry_flusher=..., eval_task_fn=...)`
- `execute_with_state(request, ...)`
- `aexecute(request, ...)`
- `aexecute_with_state(request, ...)`
- `stream(request, ...)`

This led to several architectural friction points:
1. **Parameter Leakage Across the Seam**: `app/api/routes.py` had to maintain a private `_runner_hooks()` dictionary packaging production hook functions and unpack them (`**_runner_hooks()`) into every HTTP route handler invocation (`/chat` and `/chat/stream`), violating caller leverage.
2. **Repetitive Fallback Chains**: Every execution method inside `runner.py` repeated identical 4-line fallback resolution chains (`cb_factory = callback_factory or self.callback_factory or get_langfuse_callback`).
3. **Bloated Interface Surface**: Exposing five methods (`execute`, `execute_with_state`, `aexecute`, `aexecute_with_state`, `stream`) blurred the module interface, while `execute_with_state` exposed private LangGraph state dictionaries rather than standardized `ChatResponse` objects.

To achieve clean locality and leverage, runtime hooks must be bound strictly at initialization with production defaults, and the execution interface shrunk to the request payload and runtime execution context.

---

## Decisions

### 1. Constructor-Only Hook Binding with Auto-Wired Defaults
- Bind `callback_factory`, `trace_extractor`, `telemetry_flusher`, and `eval_task_fn` strictly during `WorkflowRunner.__init__`.
- Default these arguments in `WorkflowRunner.__init__` directly to canonical production adapters (`get_langfuse_callback`, `extract_trace_id`, `flush_telemetry`, and `run_online_evaluation_task`).
- Completely remove `callback_factory`, `trace_extractor`, `telemetry_flusher`, and `eval_task_fn` parameters from `execute()`, `aexecute()`, and `stream()`.

### 2. Elimination of Transport Hook Assembly
- Permanently delete `_runner_hooks()` from `app/api/routes.py`.
- Modernize transport route endpoints to be clean, one-line callers:
  - `POST /api/chat`: `get_workflow_runner().execute(payload, background_tasks=background_tasks)`
  - `POST /api/chat/stream`: `get_workflow_runner().stream(payload)`

### 3. Interface Shrinkage: Pruning State Variants
- Prune `execute_with_state` and `aexecute_with_state` from the public interface of `WorkflowRunner`.
- The public execution interface consists strictly of:
  - `execute(request: ChatRequest, background_tasks: BackgroundTasks | None = None, **kwargs: Any) -> ChatResponse`
  - `async aexecute(request: ChatRequest, background_tasks: BackgroundTasks | None = None, **kwargs: Any) -> ChatResponse`
  - `stream(request: ChatRequest) -> AsyncGenerator[str, None]`
- Internalize state mapping logic into private helper methods (`_execute_with_state`, `_aexecute_with_state`). Callers and integration tests interact strictly through domain models (`ChatRequest` and `ChatResponse`).

### 4. Preservation of Workflow Modularity via Protocol Seam
- `WorkflowRunner` interacts with workflow topologies exclusively through the `WorkflowDefinition` protocol seam.
- The runner resolves metadata (`step_labels`, `generation_nodes`, `metadata`), state adapters (`to_initial_state`, `to_chat_response`), and evaluation payloads (`extract_eval_context`) strictly via the protocol contract.
- Zero concrete workflow classes or `isinstance` branches are permitted inside `WorkflowRunner`.

### 5. Authoritative Trace Normalization
- Centralize all trace ID sanitization (stripping hyphens, lowercasing) and contextvar binding (`structlog.contextvars.bind_contextvars`) inside `WorkflowRunner._prepare_execution`.
- Guarantee that trace context is consistently bound across LangGraph execution config, Langfuse telemetry callbacks, structured logging, `ChatResponse`, and SSE `metadata` event frames.

### 6. Strict Background Evaluation Isolation
- Online evaluation dispatch remains strictly fire-and-forget:
  - Synchronous `execute()` schedules evaluation via FastAPI `BackgroundTasks` (or falls back to an active event loop task).
  - Asynchronous `stream()` and `aexecute()` schedule evaluation via `asyncio.create_task` with task retention in `_background_tasks` to prevent premature garbage collection.
- All evaluation invocations are shielded with exception handling and structured log warnings, guaranteeing that judge latencies, Langfuse rate limits, or evaluation timeouts never cause a user chat request or stream to fail.

### 7. Clean Test Fixture Isolation
- `get_workflow_runner(...)` retains optional factory override arguments (`registry`, `callback_factory`, `trace_extractor`, `telemetry_flusher`, `eval_task_fn`) for tests requiring isolated doubles.
- Provide a clean singleton reset function `reset_workflow_runner()` incorporated into the `tests/conftest.py` autouse fixture suite to ensure test isolation without global monkeypatching.

---

## Consequences

### Positive
- **Compact & Deep Interface**: The public interface of `WorkflowRunner` shrinks significantly across all execution methods, while internalizing hook fallback resolution.
- **Maximum Caller Leverage**: Transport routes (`app/api/routes.py`) and external scripts interact with simple 1-line calls without assembling hook dictionaries.
- **Strict Locality**: Telemetry context binding, trace normalization, and evaluation scheduling concentrate in one module.
- **Robust Failure Domain**: Evaluation and telemetry errors are completely decoupled from chat response delivery.
- **Zero-Shim Test Doubles**: Unit tests inject mock callbacks or custom registries cleanly via `get_workflow_runner(...)` without monkeypatching module dictionaries.

### Negative / Trade-Offs
- Callers requiring custom telemetry handlers cannot pass per-call hook arguments to `execute()` or `stream()`; they must instantiate or resolve a configured `WorkflowRunner` instance.
