# 03: Transport Wire Seam & SseStreamAdapter

**What to build:**
A dedicated presentation adapter (`SseStreamAdapter`) residing strictly at the HTTP transport seam in `app/api/streaming.py` that translates streams of `WorkflowExecutionEvent` domain events into standard Server-Sent Event text frames formatted identically to existing client expectations.

**Blocked by:**
01: Domain Event Subsystem & Schemas

**Status:** ready-for-agent

- [ ] Create `app/api/streaming.py` defining:
  - `SseStreamAdapter` with `@classmethod async def format(events: AsyncGenerator[WorkflowExecutionEvent, None]) -> AsyncGenerator[str, None]`
  - Mapping logic:
    - `StepTransitionEvent` -> `format_sse("step", {"step": event.step})`
    - `TokenDeltaEvent` -> `format_sse("token", {"delta": event.delta})`
    - `ExecutionCompletedEvent` -> `format_sse("metadata", metadata_payload)` (including `trace_id`, `session_id`, `workflow`, `source_used`, `citations`, `trace`, `rewritten_query`, `variant_id`)
    - `ExecutionFailedEvent` -> `format_sse("error", {"error": event.error, "code": event.code})`
- [ ] Ensure `app/api/streaming.py` or `app/api/__init__.py` houses wire helpers like `format_sse` without polluting domain modules.
- [ ] Export `SseStreamAdapter` in `app/api/__init__.py`.
- [ ] Implement unit tests in `tests/api/test_sse_stream_adapter.py` verifying frame serialization, escaping, and event mapping.
