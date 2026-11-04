# Technical Specification: Extract Execution Context Binder Seam

- **ADR Reference:** `docs/adr/0021-extract-execution-context-binder-seam.md`
- **Target Subsystems:** `app/rag/workflows/`, `tests/rag/`, `tests/api/`
- **Specification Document:** `docs/spec.md` & `docs/specs/0021-extract-execution-context-binder-seam-spec.md`
- **Status:** Implemented / Verified
- **Triage Label:** `closed`

---

## Problem Statement

Following the unification of post-execution lifecycle listeners in ADR-0020, `WorkflowRunner` successfully eliminated side-effect bypasses. However, the input preparation path in `WorkflowRunner` remained an overgrown, procedural monolith:

1. **Entangled Preparation Responsibilities**: The ~220-line `_prepare_execution` method tightly coupled 3-tier profile parameter precedence resolution (Explicit overrides > Registered profile > Workflow baseline), multi-dimensional telemetry tag composition and deduplication, structlog contextvars binding, Langfuse callback instantiation, and conversational memory hydration into an un-seamed block.
2. **Untyped Internal Coupling & Positional Fragility**: `_prepare_execution` returned an untyped 5-tuple:
   `tuple[WorkflowDefinition, str | None, Any, dict[str, Any], dict[str, Any]]`
   (workflow definition, trace_id, callback, metadata, initial_state). Callers across `execute()`, `execute_with_context()`, `aexecute()`, and `stream()` were forced to unpack positional indices without type checking, inviting regression.
3. **Asymmetric Memory Seams**: Write-side turn persistence was cleanly extracted to `MemoryPersistenceListener`, but read-side conversational turn hydration remained embedded inside `WorkflowRunner`, maintaining an unnecessary coupling to `ConversationMemoryAdapter`.
4. **Synchronous Threadpool Overhead on Async Paths**: `_prepare_execution` relied on `_hydrate_memory_sync` via `run_async_safely`, forcing async streaming and execution routes through unnecessary threadpool hops rather than awaiting database queries directly on the active event loop.
5. **Testing Friction & Heavy Fixtures**: Unit tests validating execution profile overrides, model parameter inheritance, or telemetry tag composition could not test preparation in isolation; they had to instantiate full `WorkflowRunner` instances and either mock LangGraph compilation or run graph execution end-to-end.

---

## Solution

Extract a dedicated, deep `ExecutionContextBinder` module in `app/rag/workflows/binder.py` that encapsulates all pre-execution parameter resolution, memory hydration, and telemetry setup behind an immutable `BoundExecutionContext` domain value object:

1. **Dedicated Deep Binder Module (`ExecutionContextBinder`)**:
   Implement a cohesive class in `app/rag/workflows/binder.py` presenting two explicit, high-leverage entry points:
   - `bind(request, ...)`: Synchronous binding for `execute()`.
   - `abind(request, ...)`: Native asynchronous binding for `aexecute()` and `stream()`, awaiting conversational turn hydration directly on the event loop.
2. **Immutable Domain Output Model (`BoundExecutionContext`)**:
   Define a frozen domain model in `app/rag/workflows/base.py` (`frozen=True, extra="forbid"`) packaging the resolved `WorkflowDefinition`, normalized `initial_state`, pre-merged `RunnableConfig` (with callbacks, tags, metadata), `trace_id`, `session_id`, `variant_id`, and `resolved_parameters`. Provide an `as_runnable_config()` helper for immediate consumption by LangGraph execution loops.
3. **Encapsulate Read-Path Conversational Hydration**:
   Move message history retrieval, turn parsing, and rewind turn slicing (`rewind_turn_index`) completely into `ExecutionContextBinder`. `WorkflowRunner` delegates input hydration entirely to the binder on the read path.
4. **Encapsulate Observability & Logging Context**:
   `ExecutionContextBinder` encapsulates `structlog.contextvars.bind_contextvars(...)` and Langfuse callback instantiation during `bind()`/`abind()`, guaranteeing that trace identifiers, session tokens, and callbacks are primed before graph execution begins.
5. **Clean Dependency Injection & Streamlined `WorkflowRunner`**:
   `WorkflowRunner` accepts `binder: ExecutionContextBinder | None = None`. When omitted, it auto-wires a default binder from injected dependencies. Delegate `runner.memory` and `runner.resolve_workflow` to `self._binder` to maintain 100% backward compatibility. Delete `_prepare_execution`, `_hydrate_memory_sync`, and `_ahydrate_memory` from `WorkflowRunner`, shrinking it by ~250 lines.

---

## User Stories

1. As a platform developer, I want `WorkflowRunner` to delegate input preparation to `ExecutionContextBinder`, so that `WorkflowRunner` is focused strictly on graph execution orchestration and lifecycle dispatching.
2. As a test author, I want to instantiate `ExecutionContextBinder` directly with fake workflows and memory stores, so that I can test 3-tier parameter precedence and profile resolution in fast unit tests without executing LangGraph.
3. As a test author, I want to assert on `BoundExecutionContext` properties (`resolved_parameters`, `tags`, `metadata`, `initial_state`), so that I can verify execution configurations deterministically with zero mocks.
4. As an API client sending a multi-turn chat request, I want `ExecutionContextBinder.abind` to asynchronously fetch my prior session turns directly on the event loop, so that streaming responses begin with minimal latency and zero unnecessary thread switches.
5. As an API client sending an in-place turn rewind request (`rewind_turn_index=k`), I want `ExecutionContextBinder` to slice conversational history to turns `[0 ... k-1]`, so that graph nodes receive only the valid historical context.
6. As a workflow author configuring execution profiles in `WorkflowRegistry`, I want explicit request overrides (Tier 1) to take precedence over registered profile parameters (Tier 2), which in turn override workflow baseline metadata (Tier 3), so that parameter inheritance remains consistent.
7. As an observability engineer, I want `ExecutionContextBinder` to automatically compile standard telemetry tags (`workflow:<id>`, `env:<app_env>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`, `turn_count:<n>`), so that traces in Langfuse are consistently indexed across all workflows.
8. As a backend engineer debugging server logs, I want `ExecutionContextBinder` to bind `workflow`, `trace_id`, `session_id`, and `variant_id` to structlog contextvars during binding, so that all subsequent logs for the execution carry the correct correlation IDs.
9. As a developer using `WorkflowRunner.stream()`, I want the runner to unpack `bound.initial_state` and `bound.as_runnable_config()` directly into `astream_events`, so that streaming loops are concise and free of tuple index arithmetic.
10. As a developer using `WorkflowRunner.execute()`, I want synchronous binding to use `binder.bind()`, so that synchronous execution pathways continue to function seamlessly in offline batch and test environments.
11. As a legacy test suite inspecting `runner.memory`, I want `WorkflowRunner.memory` to return the binder's underlying `ConversationMemoryAdapter`, so that existing test assertions do not break.
12. As an HTTP route handler calling `runner.resolve_workflow(workflow_name)`, I want the runner to forward resolution to the binder, so that invalid workflow names continue to raise `HTTPException(status_code=400, detail="Unknown workflow: ...")`.
13. As an engineer writing custom execution harnesses, I want to inject a mock or custom `ExecutionContextBinder` into `WorkflowRunner(binder=custom_binder)`, so that I can customize execution preparation without subclassing `WorkflowRunner`.
14. As a developer inspecting `app/rag/workflows/__init__.py`, I want `ExecutionContextBinder` and `BoundExecutionContext` to be canonically exported, so that other subsystems can import them cleanly.
15. As a maintainer, I want `_prepare_execution` and internal hydration helpers deleted from `WorkflowRunner`, so that all input preparation logic is concentrated in a single, well-bounded module.

---

## Implementation Decisions

### 1. `BoundExecutionContext` Domain Model

Define the frozen domain value object in `app/rag/workflows/base.py`:

```python
class BoundExecutionContext(BaseModel):
    """Immutable domain context bundling resolved workflow, state, config, and telemetry."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    workflow: WorkflowDefinition
    initial_state: dict[str, Any]
    config: dict[str, Any]
    trace_id: str
    session_id: str | None = None
    variant_id: str | None = None
    resolved_parameters: dict[str, Any] = Field(default_factory=dict)
    hydrated_messages: list[BaseMessage] | None = None

    def as_runnable_config(self) -> dict[str, Any]:
        """Return defensive shallow copy of config suitable for LangGraph runnables."""
        return dict(self.config)
```

### 2. `ExecutionContextBinder` Module Architecture

Create `app/rag/workflows/binder.py`:

- **Constructor**:
  ```python
  class ExecutionContextBinder:
      def __init__(
          self,
          registry: WorkflowRegistry | None = None,
          *,
          memory: ConversationMemoryAdapter | None = None,
          callback_factory: Callable[..., Any] | None = None,
          trace_extractor: Callable[[Any], str | None] | None = None,
          settings: Any | None = None,
      ) -> None: ...
  ```
- **Key Methods**:
  - `bind(request, ...)`: Synchronous entry point.
  - `abind(request, ...)`: Asynchronous entry point.
  - `resolve_workflow(workflow_name)`: Registry lookup with auto-registration fallback; raises `HTTPException(status_code=400)` on missing workflow.
  - `_resolve_parameters(request, wf_def, profile, kwargs)`: Implements the 3-tier precedence resolution.
  - `_compile_telemetry(wf_def, profile, resolved_params, request, kwargs)`: Generates tags, metadata, and initializes Langfuse callback.
  - `_ahydrate_messages(request)` / `_hydrate_messages_sync(request)`: Message fetching and rewind truncation.

### 3. Streamlining `WorkflowRunner`

Refactor `WorkflowRunner` in `app/rag/workflows/runner.py`:
- Accept `binder: ExecutionContextBinder | None = None` in `__init__`.
- If `binder is None`, instantiate `ExecutionContextBinder(registry=self.registry, memory=memory, callback_factory=callback_factory, trace_extractor=trace_extractor)`.
- Replace all calls to `_prepare_execution(...)` in `execute()`, `execute_with_context()`, `aexecute()`, and `stream()` with `self._binder.bind(...)` or `await self._binder.abind(...)`.
- Delete `_prepare_execution`, `_hydrate_memory_sync`, and `_ahydrate_memory`.
- Preserve property delegates:
  ```python
  @property
  def binder(self) -> ExecutionContextBinder:
      return self._binder


  @property
  def memory(self) -> ConversationMemoryAdapter | None:
      return self._binder.memory


  def resolve_workflow(self, workflow_name: str) -> WorkflowDefinition:
      return self._binder.resolve_workflow(workflow_name)
  ```

---

## Testing Decisions

- **What makes a good test**: Tests must verify external behavior through public interfaces (`bind`, `abind`), rather than asserting on private internal helper functions.
- **Unit Test Coverage (`tests/rag/test_execution_context_binder.py`)**:
  - 3-tier parameter precedence: Explicit override beats profile beats baseline.
  - Telemetry tags & metadata compilation: Verification of `workflow:`, `variant:`, `model:`, `turn_count:` tags.
  - Conversational memory hydration & rewind: Verification that `rewind_turn_index=k` slices messages to `[:k]`.
  - Asynchronous hydration (`abind`) and synchronous hydration (`bind`).
  - Unknown workflow resolution raises `HTTPException(400)`.
- **Regression & Integration Coverage**:
  - `tests/rag/test_workflow_runner.py`: Ensure all existing runner tests pass without regression.
  - `tests/rag/test_workflow_execution.py` and `tests/api/test_routes_chat_memory.py`: Ensure streaming, async execution, and route integration remain fully functional.

---

## Out of Scope

- Modifying the `ExecutionLifecycleListener` protocol or changing post-execution side-effects (settled in ADR-0020).
- Modifying prompt store resolution or graph topology inside `app/rag/engine.py`.
- Altering HTTP route signatures or schemas in `app/api/schemas.py`.

---

## Further Notes

This refactor completes the architectural deepening of `WorkflowRunner` initiated in ADR-0010 and ADR-0020:
- **Input Seam**: `ExecutionContextBinder` (`bind` / `abind`)
- **Execution Engine**: `WorkflowRunner` (`execute` / `aexecute` / `stream`)
- **Output Seam**: `ExecutionLifecycleListener` (`on_completed`, `on_background_task`, `on_error`, `on_cancelled`)
