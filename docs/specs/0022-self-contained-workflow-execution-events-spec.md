# Technical Specification: Self-Contained Workflow Execution Events and Pure Transport Adapters

- **ADR Reference:** `docs/adr/0022-self-contained-workflow-execution-events.md`
- **Target Subsystems:** `app/rag/workflows/`, `app/api/`, `tests/api/`, `tests/rag/`
- **Specification Document:** `docs/specs/0022-self-contained-workflow-execution-events-spec.md`
- **Status:** Proposed / Ready for Review
- **Triage Label:** `ready-for-agent`

---

## Problem Statement

In ADR 0019, `WorkflowExecutionEvent` was introduced to decouple core workflow execution from HTTP Server-Sent Events (SSE) text formatting. However, an architectural fracture was left unresolved between the domain event model and the transport presentation layer:

1. **Shallow `StepTransitionEvent` Model**: `StepTransitionEvent` in `app/rag/workflows/events.py` is defined as:
   ```python
   class StepTransitionEvent(BaseModel):
       type: Literal["step"] = "step"
       step: str
   ```
   It carries only the internal graph node identifier (`step: str`), discarding the human-readable display label and emission timestamp.
2. **Presentation Seam Piercing in HTTP Route**: To satisfy the frontend wire schema (`{"step": ..., "label": ..., "timestamp": ...}`), `POST /api/chat/stream` in `app/api/routes.py` is forced to bypass the event stream seam:
   ```python
   # 19 lines of cross-seam lookup in app/api/routes.py:
   wf_input = payload.to_workflow_input()
   runner = get_workflow_runner()
   step_labels = None
   if wf_input.workflow:
       try:
           wf_def = runner.resolve_workflow(wf_input.workflow)
           step_labels = wf_def.info.step_labels if wf_def and wf_def.info else None
       except Exception:
           pass
   if step_labels is None:
       step_labels = DEFAULT_STEP_LABELS
   return StreamingResponse(
       SseStreamAdapter.format(runner.stream(wf_input), step_labels=step_labels),
       ...
   )
   ```
   The HTTP route redundantly resolves the workflow definition from `WorkflowRegistry`, extracts `info.step_labels`, manages exception fallbacks, and injects the dictionary into `SseStreamAdapter`.
3. **Leaky Presentation Adapter Interface**: `SseStreamAdapter.format` requires an external `step_labels: dict[str, str] | None` dictionary and computes timestamps (`datetime.now(UTC).timestamp()`) inside the serializer on behalf of execution milestones. It is not a pure stream translator.
4. **Multi-Transport Inconsistency**: Any new presentation adapter (e.g. WebSockets, gRPC, CLI streamer) would have to duplicate this workflow registry inspection and step label resolution logic.

---

## Solution

Seal the streaming execution seam by making `StepTransitionEvent` completely self-contained, establishing `WorkflowRunner` as the single authority for label resolution, and transforming `SseStreamAdapter` into a pure, zero-shim event stream translator:

1. **Self-Contained `StepTransitionEvent` Domain Model (`app/rag/workflows/events.py`)**:
   Add `label: str = Field(default="")` and `timestamp: float = Field(default_factory=lambda: datetime.now(UTC).timestamp())` to `StepTransitionEvent`. Implement a Pydantic `@model_validator(mode="after")` that defaults `label` to `step` if omitted, empty, or whitespace-only, ensuring `label` is always a non-empty `str`.
2. **Runner-Side Label Resolution (`app/rag/workflows/runner.py`)**:
   In `WorkflowRunner._stream_generator`, resolve the human-readable label at node transition time from the bound `WorkflowDefinition.info.step_labels` (with fallback to `DEFAULT_STEP_LABELS` or `node_name`). Emit `StepTransitionEvent(step=node_name, label=resolved_label)`.
3. **Pure Transport Adapter Interface (`app/api/streaming.py`)**:
   Remove the `step_labels` parameter from `SseStreamAdapter.format` entirely (following our Zero-Shim standard). Format `step`, `label`, and `timestamp` uniformly on every `event: step` frame directly from the domain event.
4. **Streamlined HTTP Presentation Route (`app/api/routes.py`)**:
   Delete the 19-line workflow lookup and exception-handling block in `POST /api/chat/stream`. Delegate streaming directly to `runner.stream(wf_input)` and pass the resulting domain stream straight to `SseStreamAdapter.format()`.

---

## User Stories

1. As a platform developer, I want `StepTransitionEvent` to carry its own `label` and `timestamp`, so that domain events are completely self-contained and usable across any transport without external hydration tables.
2. As a platform developer, I want `StepTransitionEvent` to automatically default `label` to `step` if not explicitly supplied, so that test fakes and internal callers never produce `None` or empty labels.
3. As an API presentation engineer, I want `POST /api/chat/stream` to delegate directly to `runner.stream(wf_input)` without resolving workflow metadata, so that the HTTP route is completely decoupled from workflow execution internals.
4. As a transport developer, I want `SseStreamAdapter.format` to take only an `AsyncIterable[WorkflowExecutionEvent]` without requiring a `step_labels` dictionary, so that the adapter acts as a pure stream serializer.
5. As a frontend client developer, I want `event: step` SSE frames to always contain consistent `step`, `label`, and `timestamp` fields, so that UI progression indicators render deterministically without missing properties.
6. As a workflow author, I want my workflow's custom `WorkflowInfo.step_labels` to be resolved automatically by `WorkflowRunner` during streaming, so that custom topologies display their configured step descriptions without presentation-layer changes.
7. As a test author, I want to construct `StepTransitionEvent(step="retrieve")` in unit tests without specifying labels or timestamps, so that tests remain concise while still receiving valid defaults.
8. As a maintainer adhering to the Zero-Shim standard, I want the vestigial `step_labels` parameter purged from `SseStreamAdapter.format` rather than retained as a deprecated shim, so that our interfaces remain minimal and deep.

---

## Implementation Decisions

### 1. `StepTransitionEvent` Domain Model (`app/rag/workflows/events.py`)

Enhance `StepTransitionEvent` with `label` and `timestamp`, backed by a model validator:

```python
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from pydantic import BaseModel, Field, model_validator


class StepTransitionEvent(BaseModel):
    """Emitted when the execution graph transitions between discrete nodes."""

    type: Literal["step"] = "step"
    step: str = Field(description="Name of the node entered (e.g. 'retrieve', 'grade_evidence')")
    label: str = Field(default="", description="Human-readable progression label")
    timestamp: float = Field(
        default_factory=lambda: datetime.now(UTC).timestamp(),
        description="Chronological UTC milestone timestamp in seconds",
    )

    @model_validator(mode="after")
    def _ensure_label(self) -> Self:
        if not self.label or not self.label.strip():
            self.label = self.step
        return self
```

### 2. Runner Label Resolution (`app/rag/workflows/runner.py`)

Update `_stream_generator` to resolve `label` at event creation:

```python
# app/rag/workflows/runner.py (_stream_generator)
step_labels = (wf_def.info.step_labels if wf_def and wf_def.info else None) or DEFAULT_STEP_LABELS
generation_nodes = (
    wf_def.info.generation_nodes if wf_def and wf_def.info else None
) or DEFAULT_GENERATION_NODES
stream_steps = getattr(request, "stream_steps", True)

async for event in graph.astream_events(initial_state, version="v2", config=config if config else None):
    ev_type = event.get("event")
    node_name = event.get("metadata", {}).get("langgraph_node")

    # 1. Step transitions
    if stream_steps and ev_type == "on_chain_start" and node_name in step_labels:
        resolved_label = step_labels.get(node_name, node_name)
        yield StepTransitionEvent(step=node_name, label=resolved_label)
```

### 3. Pure `SseStreamAdapter.format` (`app/api/streaming.py`)

Purge `step_labels` parameter and uniformly serialize all fields:

```python
# app/api/streaming.py
class SseStreamAdapter:
    """Presentation adapter translating domain WorkflowExecutionEvent streams into HTTP SSE text frames."""

    @classmethod
    async def format(
        cls,
        events: AsyncIterable[WorkflowExecutionEvent],
    ) -> AsyncGenerator[str, None]:
        """Convert asynchronous workflow domain events into standard SSE text frames."""
        async for event in events:
            if isinstance(event, StepTransitionEvent):
                yield format_sse(
                    "step",
                    {
                        "step": event.step,
                        "label": event.label,
                        "timestamp": event.timestamp,
                    },
                )
            elif isinstance(event, TokenDeltaEvent):
                yield format_sse("token", {"delta": event.delta})
            elif isinstance(event, ExecutionCompletedEvent):
                ...
            elif isinstance(event, ExecutionFailedEvent):
                ...
```

### 4. Collapse HTTP Presentation Route (`app/api/routes.py`)

Collapse `POST /api/chat/stream` into a 2-line streaming pipeline:

```python
# app/api/routes.py
@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    wf_input = payload.to_workflow_input()
    runner = get_workflow_runner()
    return StreamingResponse(
        SseStreamAdapter.format(runner.stream(wf_input)),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
```

---

## Testing Decisions

### 1. What Makes a Good Test
- Tests must assert on public behavior and domain models without inspecting private helper functions.
- Unit tests verify event model defaulting and timestamp assignment deterministically.
- Adapter tests verify pure serialization without injecting mocked dictionary parameters.
- Integration tests verify that `POST /api/chat/stream` produces valid SSE frames with `step`, `label`, and `timestamp` across all default workflows.

### 2. Unit Test Plan (`tests/rag/test_workflow_events.py` & `tests/api/test_sse_stream_adapter.py`)
- **`tests/rag/test_workflow_events.py`**:
  - Test `StepTransitionEvent(step="retrieve")` defaults `label="retrieve"`.
  - Test `StepTransitionEvent(step="retrieve", label="Searching KB...")` preserves explicit label.
  - Test `StepTransitionEvent(step="retrieve", label="")` falls back to `"retrieve"`.
  - Test `StepTransitionEvent(step="retrieve").timestamp` is a valid positive float close to current time.
- **`tests/api/test_sse_stream_adapter.py`**:
  - Update `test_adapter_step_transition_event` to verify that `event: step` frame contains `{"step": "retrieve", "label": "retrieve", "timestamp": ...}`.
  - Update `test_adapter_step_transition_with_step_labels_enrichment` to test self-contained `StepTransitionEvent(step="retrieve_kb", label="Retrieving documents...")` serialization without passing `step_labels` to `format()`.
  - Verify `test_adapter_full_lifecycle_sequence` formats step frames with label and timestamp.

### 3. Integration & Regression Plan (`tests/rag/test_workflow_runner.py` & `tests/test_chat_stream.py`)
- **`tests/rag/test_workflow_runner.py`**:
  - Verify `runner.stream(request)` emits `StepTransitionEvent` with `label` populated from workflow `info.step_labels`.
- **`tests/test_chat_stream.py`**:
  - Verify `POST /api/chat/stream` emits SSE frames containing `step`, `label`, and `timestamp` for live workflows.
  - Verify `stream_steps=False` still suppresses step frames completely.

---

## Out of Scope

- Modifying `TokenDeltaEvent`, `ExecutionCompletedEvent`, or `ExecutionFailedEvent` domain models.
- Modifying `ExecutionContextBinder` or parameter resolution (settled in ADR-0021).
- Modifying `ExecutionLifecycleListener` or turn persistence (settled in ADR-0020).
- Adding new transport protocols (e.g. WebSocket or gRPC); this spec prepares the clean seam for future transports.

---

## Further Notes

This refactor eliminates the final presentation-layer boundary leak identified during the architecture review of ADR-0019 through ADR-0021:
- **Pre-Execution**: `ExecutionContextBinder` (sealed input prep)
- **Execution**: `WorkflowRunner` (sealed graph execution & event emission)
- **Side-Effects**: `ExecutionLifecycleListener` (sealed listener dispatch)
- **Presentation**: `SseStreamAdapter` (pure event stream translation)
