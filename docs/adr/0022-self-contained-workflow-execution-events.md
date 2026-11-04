# ADR 0022: Self-Contained Workflow Execution Events and Pure Transport Adapters

## Status
Accepted (Extends ADR 0019 and ADR 0020)

## Context
In ADR 0019, we introduced `WorkflowExecutionEvent` (`StepTransitionEvent`, `TokenDeltaEvent`, `ExecutionCompletedEvent`, `ExecutionFailedEvent`) to decouple core workflow execution from HTTP Server-Sent Events (SSE) wire serialization. 

However, an architectural boundary leak persisted at the presentation seam:
1. **Shallow Step Event**: `StepTransitionEvent` only carried `step: str` (the internal graph node identifier), omitting the human-readable display label and emission timestamp.
2. **Cross-Seam Inspection**: To populate the `event: step` SSE frame with `{"step": ..., "label": ..., "timestamp": ...}`, `POST /api/chat/stream` in `app/api/routes.py` had to breach the execution boundary. The HTTP route inspected `WorkflowRunner`, re-resolved the workflow topology from `WorkflowRegistry`, extracted `info.step_labels`, and injected them into `SseStreamAdapter.format(..., step_labels=step_labels)`.
3. **Leaky Transport Adapter**: `SseStreamAdapter` was not a pure event stream translator; it required external step label dictionary injection and generated timestamps on behalf of execution milestones.
4. **Multi-Transport Duplication**: Any alternative transport adapter (e.g. WebSockets, gRPC, CLI streamer) would be forced to duplicate this registry lookup and hydration logic.

To achieve strict modularity, complete transport-agnostic execution, and zero cross-boundary leakage, domain execution events must be self-contained.

---

## Decisions

### 1. Self-Contained `StepTransitionEvent` (`app/rag/workflows/events.py`)
Enrich `StepTransitionEvent` to carry its own presentation metadata:
- `step: str`: Canonical graph node identifier.
- `label: str`: Human-readable progression label. Validated via a Pydantic model validator to fall back to `step` if omitted or empty, guaranteeing `label` is always a non-empty `str`.
- `timestamp: float`: Chronological milestone timestamp defaulting to `datetime.now(UTC).timestamp()`.

### 2. Runner-Side Label Resolution Authority (`app/rag/workflows/runner.py`)
`WorkflowRunner._stream_generator` is the sole authority responsible for resolving step labels at emission time. It maps `node_name` against the active workflow's `step_labels` (with fallback to `DEFAULT_STEP_LABELS` or `node_name`), yielding fully hydrated `StepTransitionEvent(step=node_name, label=resolved_label)`.

### 3. Pure Stream Transport Adapter (`app/api/streaming.py`)
In accordance with our Zero-Shim architecture standard:
- Prune the `step_labels` parameter from `SseStreamAdapter.format` entirely.
- `SseStreamAdapter.format` acts as a pure stream transformer (`AsyncIterable[WorkflowExecutionEvent] -> AsyncGenerator[str, None]`), serializing `{"step": event.step, "label": event.label, "timestamp": event.timestamp}` uniformly on every `step` frame.

### 4. Zero-Inspection HTTP Presentation Route (`app/api/routes.py`)
Delete the 19-line workflow lookup, exception handling, and label fallback block from `POST /api/chat/stream`. The route delegates directly to `runner.stream(wf_input)` and passes the domain stream straight into `SseStreamAdapter.format()`.

---

## Consequences

### Positive
- **Strict Seam Integrity**: Presentation routes and transport adapters have zero knowledge of `WorkflowRegistry`, graph topologies, or step label tables.
- **High Leverage**: Any new transport adapter (WebSockets, terminal UI, push notifications) instantly receives fully labeled, timestamped milestones with zero bespoke hydration.
- **Locality**: Step progression labels are defined on `WorkflowDefinition.info.step_labels` and resolved strictly within `WorkflowRunner`.
- **Zero-Shim Simplicity**: No deprecated parameter shims or dual-path branching in `SseStreamAdapter`.
- **Testability**: Tests for streaming routes and transport adapters assert against self-contained domain events without mocking workflow registries or dictionary lookups.

### Negative / Trade-Offs
- Existing unit tests that invoke `SseStreamAdapter.format(..., step_labels=...)` must be updated to pass self-contained `StepTransitionEvent(step=..., label=...)` objects.
