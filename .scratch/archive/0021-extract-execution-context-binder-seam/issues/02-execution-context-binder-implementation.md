# Ticket 02: ExecutionContextBinder Implementation & Unit Tests

- **Part of:** ADR-0021 (`docs/specs/0021-extract-execution-context-binder-seam-spec.md`)
- **Blockers:** Ticket 01
- **Scope:** `app/rag/workflows/binder.py`, `app/rag/workflows/__init__.py`, `tests/rag/test_execution_context_binder.py`
- **Labels:** `closed`
- **Status:** closed

---

## Objective

Implement the deep `ExecutionContextBinder` module in `app/rag/workflows/binder.py` and write exhaustive unit tests in `tests/rag/test_execution_context_binder.py`.

---

## Detailed Requirements

1. **`ExecutionContextBinder` Class**:
   - `__init__(self, registry=None, *, memory=None, callback_factory=None, trace_extractor=None, settings=None)`
   - Properties:
     - `registry`: The active `WorkflowRegistry`.
     - `memory`: The active `ConversationMemoryAdapter | None`.
     - `callback_factory`: Injected callback factory or fallback to `get_langfuse_callback`.
     - `trace_extractor`: Injected trace extractor or fallback to `extract_trace_id`.
     - `settings`: Application settings or fallback to `get_settings()`.
   - Core Methods:
     - `resolve_workflow(self, workflow_name: str) -> WorkflowDefinition`: Resolves workflow from registry; auto-registers defaults if empty; raises `HTTPException(status_code=400, detail=f"Unknown workflow: {workflow_name}")` on missing ID.
     - `bind(self, request: WorkflowInput, *, streaming: bool = False, callback: Any | None = None, metadata: dict[str, Any] | None = None, config: dict[str, Any] | None = None, hydrated_messages: Sequence[BaseMessage | dict[str, Any]] | None = None, **kwargs: Any) -> BoundExecutionContext`: Synchronous preparation path.
     - `async abind(self, request: WorkflowInput, *, streaming: bool = False, callback: Any | None = None, metadata: dict[str, Any] | None = None, config: dict[str, Any] | None = None, hydrated_messages: Sequence[BaseMessage | dict[str, Any]] | None = None, **kwargs: Any) -> BoundExecutionContext`: Asynchronous preparation path.

2. **Internal Procedures Encapsulated**:
   - **3-Tier Parameter Precedence**:
     - Tier 1: Explicit overrides (`request.parameters`, `kwargs["parameters"]`, specific kwargs like `model_name`, `temperature`).
     - Tier 2: Registered profile parameters (`profile.parameters`, `profile.model_name`, etc. if `variant_id` resolved).
     - Tier 3: Workflow baseline metadata (`wf_def.info.metadata["parameters"]`).
   - **Telemetry & Contextvars**:
     - Deduplicated tag compilation (`workflow:`, `env:`, `variant:`, `model:`, `prompt:`, `turn_count:`).
     - Langfuse callback handler construction via `callback_factory`.
     - Trace ID extraction with UUID v4 fallback.
     - `structlog.contextvars.bind_contextvars(workflow=..., trace_id=..., session_id=..., variant_id=...)`.
   - **Memory Hydration & Rewind**:
     - `_ahydrate_messages(request)`: Queries `self.memory.aget_turns(session_id)`.
     - Slices turns when `request.rewind_turn_index` is provided: `turns[:rewind_turn_index]`.
     - Converts turns into alternating `HumanMessage` / `AIMessage` pairs.
     - `_hydrate_messages_sync(request)`: Delegates to `run_async_safely(self._ahydrate_messages, request)`.

3. **Exhaustive Unit Tests (`tests/rag/test_execution_context_binder.py`)**:
   - Test 3-tier parameter precedence in isolation with zero LangGraph mocks.
   - Test telemetry tag compilation across baseline, profile, and explicit overrides.
   - Test message hydration and rewind slicing (`rewind_turn_index=1` returns only turn 0).
   - Test synchronous `bind()` and asynchronous `abind()`.
   - Test `resolve_workflow` error raises `HTTPException(status_code=400)`.

---

## Verification Criteria

- All unit tests in `tests/rag/test_execution_context_binder.py` pass.
- `ExecutionContextBinder` exports cleanly from `app/rag/workflows`.
