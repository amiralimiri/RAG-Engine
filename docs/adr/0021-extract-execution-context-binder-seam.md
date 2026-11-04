# ADR 0021: Extract Execution Context Binder Seam

## Status
Accepted (Extends ADR 0010, ADR 0012, ADR 0019, and ADR 0020)

## Context
ADR-0020 unified write-path side-effects across all execution pathways in `WorkflowRunner` behind the `ExecutionLifecycleListener` seam. However, execution preparation remained an overgrown internal procedure (`_prepare_execution`, ~220 lines):

1. **Entangled Preparation Responsibilities**: `_prepare_execution` tangled 3-tier parameter precedence resolution (Explicit overrides > Registered profile > Workflow baseline), telemetry tag synthesis, structlog contextvars binding, Langfuse callback instantiation, and conversational memory hydration in a single block.
2. **Untyped Internal Coupling**: The procedure returned an untyped 5-tuple (`tuple[WorkflowDefinition, str | None, Any, dict[str, Any], dict[str, Any]]`), forcing callers to handle positional indices without type safety.
3. **Asymmetric Memory Lifecycles**: Write-path turn persistence was cleanly delegated to `MemoryPersistenceListener`, but read-path turn hydration remained embedded inside `WorkflowRunner`.
4. **Testing Friction**: Verifying parameter precedence or telemetry tags required spinning up full `WorkflowRunner` instances and faking or executing LangGraph runs.

## Decisions

### 1. Extract `ExecutionContextBinder` Module
Create a dedicated deep module in `app/rag/workflows/binder.py` presenting a cohesive interface:
- `bind(request, ...)`: Synchronous binding for `execute()`.
- `abind(request, ...)`: Native asynchronous binding for `aexecute()` and `stream()`, awaiting conversational turn hydration directly on the event loop.

### 2. Immutable `BoundExecutionContext` Domain Model
Define a frozen domain model `BoundExecutionContext` (`frozen=True, extra="forbid"`) in `app/rag/workflows/base.py`:
- `workflow: WorkflowDefinition`
- `initial_state: dict[str, Any]`
- `config: dict[str, Any]` (RunnableConfig with pre-populated `callbacks`, `tags`, and `metadata`)
- `trace_id: str`
- `session_id: str | None`
- `variant_id: str | None`
- `resolved_parameters: dict[str, Any]`
- `hydrated_messages: list[BaseMessage] | None`
- Helper method `as_runnable_config() -> dict[str, Any]` for immediate consumption by LangGraph execution loops.

### 3. Read-Path Memory Hydration Boundary
Inject `ConversationMemoryAdapter` directly into `ExecutionContextBinder`. Slicing of turns for rewind requests (`rewind_turn_index`) and message deserialization are fully absorbed into the binder. `WorkflowRunner` no longer interacts with `ConversationMemoryAdapter`.

### 4. Telemetry Context Encapsulation
`ExecutionContextBinder` encapsulates `structlog.contextvars.bind_contextvars(...)` and Langfuse callback initialization during `bind()`/`abind()`, ensuring logging context and trace callbacks are fully primed before graph execution begins.

### 5. Dependency Injection in `WorkflowRunner`
- `WorkflowRunner` accepts `binder: ExecutionContextBinder | None = None`. When omitted, it auto-wires a default `ExecutionContextBinder` from injected `registry`, `memory`, `callback_factory`, and `trace_extractor`.
- `runner.memory` and `runner.resolve_workflow(workflow_name)` are preserved as read-only delegations for zero breaking changes.
- Unknown workflows raise `HTTPException(status_code=400, detail=f"Unknown workflow: {workflow_name}")` for backwards compatibility.

## Consequences

- **Locality**: 3-tier parameter resolution, telemetry tags, and memory hydration concentrate in `ExecutionContextBinder`.
- **Leverage**: All execution modes (`execute`, `aexecute`, `stream`) share a single, strongly-typed execution context.
- **Testability**: Configuration precedence and telemetry tag rules can be verified in fast, isolated unit tests without mocking LangGraph graphs.
- **Codebase Deepening**: `WorkflowRunner` shrinks by ~250 lines, focusing strictly on graph runtime orchestration and lifecycle dispatch.
