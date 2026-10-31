# Technical Specification: Internalize Runtime Hooks into WorkflowRunner

- **ADR Reference:** `docs/adr/0012-internalize-runtime-hooks-into-workflow-runner.md`
- **Target Version:** `0.12.0`
- **Status:** Proposed

---

## 1. Problem Statement

In ADR-0010, `WorkflowRunner` (`app/rag/workflows/runner.py`) was introduced to unify workflow execution, token delta streaming, telemetry context binding, and background online evaluation dispatch. However, to preserve flexibility across earlier milestones, the runtime hooks (`callback_factory`, `trace_extractor`, `telemetry_flusher`, and `eval_task_fn`) were left as optional constructor parameters *and* duplicated as optional keyword parameters across every single public execution method:
- `execute(...)`
- `execute_with_state(...)`
- `aexecute(...)`
- `aexecute_with_state(...)`
- `stream(...)`

This caused significant architectural friction:
1. **Transport Layer Coupling & Parameter Leakage**: In `app/api/routes.py`, HTTP endpoint handlers (`/chat` and `/chat/stream`) cannot simply pass the incoming `ChatRequest` to `WorkflowRunner`. Instead, `routes.py` maintains an internal helper `_runner_hooks()` that packages four application-level functions (`get_langfuse_callback`, `extract_trace_id`, `flush_telemetry`, `run_online_evaluation_task`) and unpacks them via `**_runner_hooks()` into every execution call.
2. **Repetitive Fallback Chains**: Inside `runner.py`, every execution method implements identical boilerplate fallback logic:
   ```python
   cb_factory = callback_factory or self.callback_factory or get_langfuse_callback
   tr_extractor = trace_extractor or self.trace_extractor or extract_trace_id
   tel_flusher = telemetry_flusher or self.telemetry_flusher or flush_telemetry
   ev_task_fn = eval_task_fn or self.eval_task_fn or run_online_evaluation_task
   ```
3. **Bloated Interface Surface**: Exposing five methods (`execute`, `execute_with_state`, `aexecute`, `aexecute_with_state`, `stream`) dilutes the clarity of the runner seam. Specifically, `execute_with_state` returns private LangGraph state dictionaries rather than standardized domain models (`ChatResponse`), leaking graph-internal keys into callers.
4. **Scattered Trace Lifecycle Authority**: Trace ID sanitization (stripping hyphens and lowercasing) and contextvar binding are duplicated across routes and runner execution paths rather than being authoritatively owned by the runner.

---

## 2. Solution & Goals

1. **Constructor-Only Hook Binding with Canonical Defaults**:
   - Bind all telemetry and evaluation hooks strictly during `WorkflowRunner.__init__`.
   - Default all hook parameters directly to canonical production adapters:
     - `callback_factory: Callable[..., Any] = get_langfuse_callback`
     - `trace_extractor: Callable[[Any], str | None] = extract_trace_id`
     - `telemetry_flusher: Callable[[], None] = flush_telemetry`
     - `eval_task_fn: Callable[..., Any] = run_online_evaluation_task`
   - Prune all four hook parameters from `execute()`, `aexecute()`, and `stream()`.

2. **Clean Transport Routes**:
   - Permanently delete `_runner_hooks()` from `app/api/routes.py`.
   - Simplify transport routes into pure 1-line callers:
     - `POST /api/chat`: `get_workflow_runner().execute(payload, background_tasks=background_tasks)`
     - `POST /api/chat/stream`: `get_workflow_runner().stream(payload)`

3. **Consolidated Public Execution Seam**:
   - Prune `execute_with_state` and `aexecute_with_state` from the public interface, retaining them strictly as private internal helpers (`_execute_with_state`, `_aexecute_with_state`).
   - The public execution seam consists exclusively of:
     - `execute(request: ChatRequest, background_tasks: BackgroundTasks | None = None, **kwargs: Any) -> ChatResponse`
     - `async aexecute(request: ChatRequest, background_tasks: BackgroundTasks | None = None, **kwargs: Any) -> ChatResponse`
     - `stream(request: ChatRequest) -> AsyncGenerator[str, None]`

4. **Strict Protocol-Driven Modularity**:
   - `WorkflowRunner` interacts with workflow graphs strictly through the `WorkflowDefinition` protocol seam (`to_initial_state`, `to_chat_response`, `extract_eval_context`, `get_compiled_graph`, and `info`).
   - Zero concrete workflow classes or `isinstance` branches inside `WorkflowRunner`.

5. **Authoritative Trace Lifecycle Normalization**:
   - Centralize all trace ID sanitization (hyphen removal, lowercase formatting) and contextvar binding (`structlog.contextvars.bind_contextvars`) inside `WorkflowRunner._prepare_execution`.
   - Ensure consistent trace context across LangGraph config, Langfuse callbacks, structured logs, `ChatResponse`, and SSE `metadata` events.

6. **Shielded Fire-and-Forget Background Evaluation**:
   - Synchronous `execute()` schedules evaluation via FastAPI `BackgroundTasks` (or falls back to an active event loop task).
   - Asynchronous `stream()` and `aexecute()` schedule evaluation via `asyncio.create_task` retained in `_background_tasks` to prevent premature garbage collection.
   - All evaluation tasks are wrapped in dedicated try/except blocks with structured log warnings so evaluation errors never break user chat requests.

7. **Zero-Shim Test Isolation & Modernized Fixtures**:
   - `get_workflow_runner(...)` retains constructor override parameters (`registry`, `callback_factory`, etc.) for clean dependency injection in unit tests.
   - Introduce `reset_workflow_runner()` in `runner.py` and register an autouse fixture in `tests/conftest.py` ensuring the global runner singleton is cleanly reset before and after every test run.

---

## 3. User Stories

1. **As an API transport developer**, I want `app/api/routes.py` to invoke `WorkflowRunner.execute()` and `WorkflowRunner.stream()` without assembling private hook dictionaries, so that the HTTP routing layer is decoupled from telemetry and evaluation internals.
2. **As a workflow maintainer**, I want `WorkflowRunner` to manage hook resolution and fallback logic once in `__init__`, so that execution methods are free from repetitive parameter plumbing and boilerplate chaining.
3. **As a core engine developer**, I want `WorkflowRunner.execute` and `WorkflowRunner.aexecute` to return standardized `ChatResponse` objects, preventing internal graph state dictionaries from leaking across caller seams.
4. **As an evaluation engineer**, I want online evaluation tasks to run in shielded background tasks, so that external LLM judge latencies or Langfuse network errors can never degrade or crash user chat responses.
5. **As an observability engineer**, I want `WorkflowRunner` to be the single source of truth for trace ID sanitization and contextvar binding, ensuring uniform trace correlation across logs, callbacks, and SSE streams.
6. **As a test author**, I want an autouse `clean_runner` fixture in `tests/conftest.py` that resets the runner singleton between tests, allowing me to inject mock callbacks without polluting subsequent test cases.

---

## 4. Exact Method Signatures & Interface Seams

### 4.1. `WorkflowRunner.__init__` & Helper Factory

#### Before
```python
class WorkflowRunner:
    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        *,
        callback_factory: Callable[..., Any] | None = None,
        trace_extractor: Callable[[Any], str | None] | None = None,
        telemetry_flusher: Callable[[], None] | None = None,
        eval_task_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else get_workflow_registry()
        self.callback_factory = callback_factory
        self.trace_extractor = trace_extractor
        self.telemetry_flusher = telemetry_flusher
        self.eval_task_fn = eval_task_fn
        self._background_tasks: set[asyncio.Task[Any]] = set()


def get_workflow_runner(
    registry: WorkflowRegistry | None = None,
    *,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
) -> WorkflowRunner:
    ...
```

#### After
```python
class WorkflowRunner:
    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        *,
        callback_factory: Callable[..., Any] | None = None,
        trace_extractor: Callable[[Any], str | None] | None = None,
        telemetry_flusher: Callable[[], None] | None = None,
        eval_task_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else get_workflow_registry()
        # Bound once at construction time; defaults directly to canonical production adapters
        self.callback_factory = callback_factory if callback_factory is not None else get_langfuse_callback
        self.trace_extractor = trace_extractor if trace_extractor is not None else extract_trace_id
        self.telemetry_flusher = telemetry_flusher if telemetry_flusher is not None else flush_telemetry
        self.eval_task_fn = eval_task_fn if eval_task_fn is not None else run_online_evaluation_task
        self._background_tasks: set[asyncio.Task[Any]] = set()


_default_runner: WorkflowRunner | None = None


def get_workflow_runner(
    registry: WorkflowRegistry | None = None,
    *,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
) -> WorkflowRunner:
    """Return the global default WorkflowRunner singleton or a customized instance."""
    global _default_runner
    if any(arg is not None for arg in (registry, callback_factory, trace_extractor, telemetry_flusher, eval_task_fn)):
        return WorkflowRunner(
            registry=registry,
            callback_factory=callback_factory,
            trace_extractor=trace_extractor,
            telemetry_flusher=telemetry_flusher,
            eval_task_fn=eval_task_fn,
        )
    if _default_runner is None:
        _default_runner = WorkflowRunner()
    return _default_runner


def reset_workflow_runner() -> None:
    """Reset the global default WorkflowRunner singleton (for test isolation)."""
    global _default_runner
    _default_runner = None
```

---

### 4.2. Synchronous Execution (`execute`)

#### Before
```python
def execute(
    self,
    request: ChatRequest,
    background_tasks: BackgroundTasks | None = None,
    *,
    callback: Any = None,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> ChatResponse:
    ...
```

#### After
```python
def execute(
    self,
    request: ChatRequest,
    background_tasks: BackgroundTasks | None = None,
    *,
    callback: Any = None,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> ChatResponse:
    """Synchronously execute a registered workflow and return ChatResponse."""
    response, _ = self._execute_with_state(
        request,
        background_tasks=background_tasks,
        callback=callback,
        metadata=metadata,
        config=config,
        **kwargs,
    )
    return response
```

---

### 4.3. Asynchronous Execution (`aexecute`)

#### Before
```python
async def aexecute(
    self,
    request: ChatRequest,
    background_tasks: BackgroundTasks | None = None,
    *,
    callback: Any = None,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> ChatResponse:
    ...
```

#### After
```python
async def aexecute(
    self,
    request: ChatRequest,
    background_tasks: BackgroundTasks | None = None,
    *,
    callback: Any = None,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> ChatResponse:
    """Asynchronously execute a registered workflow and return ChatResponse."""
    response, _ = await self._aexecute_with_state(
        request,
        background_tasks=background_tasks,
        callback=callback,
        metadata=metadata,
        config=config,
        **kwargs,
    )
    return response
```

---

### 4.4. Streaming Execution (`stream`)

#### Before
```python
def stream(
    self,
    request: ChatRequest,
    *,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
) -> AsyncGenerator[str, None]:
    ...
```

#### After
```python
def stream(
    self,
    request: ChatRequest,
) -> AsyncGenerator[str, None]:
    """Validate workflow and return an asynchronous generator yielding pre-formatted SSE frames."""
    self._resolve_workflow(request.workflow)
    return self._stream_generator(request)
```

---

### 4.5. Internal State Helpers (Private)

```python
def _execute_with_state(
    self,
    request: ChatRequest,
    background_tasks: BackgroundTasks | None = None,
    *,
    callback: Any = None,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[ChatResponse, dict[str, Any]]:
    """Internal synchronous execution returning (ChatResponse, final_state)."""
    ...

async def _aexecute_with_state(
    self,
    request: ChatRequest,
    background_tasks: BackgroundTasks | None = None,
    *,
    callback: Any = None,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[ChatResponse, dict[str, Any]]:
    """Internal asynchronous execution returning (ChatResponse, final_state)."""
    ...
```

---

## 5. Transport Layer Modifications (`app/api/routes.py`)

### 5.1. Deletion of `_runner_hooks()`
The following helper function in `app/api/routes.py` is permanently deleted:
```python
# DELETED:
# def _runner_hooks() -> dict[str, Any]:
#     return {
#         "callback_factory": get_langfuse_callback, "trace_extractor": extract_trace_id,
#         "telemetry_flusher": flush_telemetry, "eval_task_fn": run_online_evaluation_task,
#     }
```

### 5.2. Simplified Route Endpoints
```python
@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, background_tasks: BackgroundTasks):
    return get_workflow_runner().execute(payload, background_tasks=background_tasks)


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    return StreamingResponse(
        get_workflow_runner().stream(payload),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
```

---

## 6. Background Task Shielding & Error Handling

### 6.1. Shielded Online Evaluation Dispatch
In `WorkflowRunner._finalize_execution` (sync HTTP) and `WorkflowRunner._stream_generator` (streaming):

```python
if resolved_tid:
    eval_ctx = wf_def.extract_eval_context(final_state)
    source_used = ...
    retrieved_contexts = ...

    async def _safe_eval_wrapper() -> None:
        try:
            await self.eval_task_fn(
                trace_id=resolved_tid,
                question=request.question,
                answer=final_answer,
                source_used=source_used,
                retrieved_contexts=retrieved_contexts,
            )
        except Exception as exc:
            logger.warning(
                "online_evaluation_failed_shielded",
                trace_id=resolved_tid,
                error=str(exc),
            )

    if background_tasks is not None:
        background_tasks.add_task(
            self.eval_task_fn,
            trace_id=resolved_tid,
            question=request.question,
            answer=chat_response.answer,
            source_used=source_used,
            retrieved_contexts=retrieved_contexts,
        )
    else:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            task = loop.create_task(_safe_eval_wrapper())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        else:
            logger.debug("No active event loop for background evaluation scheduling")
```

### 6.2. Invariant Guarantees
1. **User Isolation**: An uncaught exception, timeout, or HTTP 401/429 within `eval_task_fn` is logged via `structlog.warning` and discarded. It cannot alter the HTTP response status or raise an exception in the user's thread/stream.
2. **Telemetry Flushing**: `self.telemetry_flusher()` is guaranteed to execute in `finally:` blocks on completion of `execute()` and termination of the `stream()` generator, wrapped in `try/except` to prevent flushing errors from overriding completed responses.

---

## 7. Test Strategy & Migration Plan

### 7.1. Conftest Autouse Reset Fixture (`tests/conftest.py`)
Add `clean_runner` fixture to ensure test isolation:

```python
from app.rag.workflows.runner import reset_workflow_runner

@pytest.fixture(autouse=True)
def clean_runner():
    """Autouse fixture resetting global WorkflowRunner singleton before and after each test."""
    reset_workflow_runner()
    yield
    reset_workflow_runner()
```

### 7.2. Test Migration Breakdown

| Test File | Current Pattern | Migrated Pattern |
|---|---|---|
| `tests/rag/test_workflow_runner.py` | Calls `runner.execute(..., callback_factory=...)` or `runner.stream(..., callback_factory=...)` | Instantiate `runner = WorkflowRunner(registry=..., callback_factory=...)` and invoke `runner.execute(req)` / `runner.stream(req)` |
| `tests/rag/test_workflow_runner.py` | Direct calls to `runner.execute_with_state(...)` | Update unit assertions to call `runner._execute_with_state(...)` or assert against `ChatResponse` |
| `tests/test_jev_telemetry.py` | Calls `runner.execute_with_state(...)` | Update fixture to use `runner._execute_with_state(request)` |
| `tests/test_llm_telemetry.py` | Calls `runner.execute_with_state(...)` | Update fixture to use `runner._execute_with_state(request)` |
| `tests/test_retrieved_contexts.py` | Calls `runner.execute_with_state(...)` | Update line 319 to `runner._execute_with_state(...)` |
| `tests/test_experiment_runner.py` | Mocks `mock_runner.execute_with_state` | Retain mock or update to `_execute_with_state` / `execute` |
| `evaluation/run_experiment.py` | Calls `active_runner.execute_with_state(req)` | Update line 134 to `active_runner._execute_with_state(req)` |
| `tests/api/test_routes_multi_workflow.py` | Verifies `/api/chat` and `/api/chat/stream` HTTP behavior | No test changes required; routes now call `get_workflow_runner().execute()` and `stream()` cleanly |

---

## 8. Out of Scope

- Merging or rewriting the `TelemetryAdapter` (Candidate #2).
- Rewriting the Prompt CMS invoker in `engine.py` (Candidate #3).
- Re-architecting offline DeepEval judge singletons in `evaluation/judge.py` (Candidate #4).
- Modifying `WorkflowDefinition` state conversion protocols (`to_initial_state`, `to_chat_response`).

---

## 9. Verification & Conformance Acceptance

The implementation will be considered complete when:
1. `app/api/routes.py` contains zero occurrences of `_runner_hooks` or `**_runner_hooks()`.
2. `WorkflowRunner.execute()`, `aexecute()`, and `stream()` accept zero hook override arguments.
3. `WorkflowRunner.__init__` binds canonical defaults for all hooks.
4. `reset_workflow_runner()` is executed in `tests/conftest.py` between every test run.
5. The full regression test suite (`.\.venv\Scripts\pytest.exe`) passes with zero failures.
