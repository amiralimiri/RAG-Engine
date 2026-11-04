# Ticket 03: Pure SseStreamAdapter & Presentation Test Alignment

- **Part of:** ADR-0022 (`docs/specs/0022-self-contained-workflow-execution-events-spec.md`)
- **Blockers:** Ticket 01, Ticket 02
- **Scope:** `app/api/streaming.py`, `tests/api/test_sse_stream_adapter.py`
- **Labels:** `ready-for-agent`

---

## Objective

Prune the `step_labels` parameter from `SseStreamAdapter.format` in `app/api/streaming.py` in strict accordance with the Zero-Shim standard, uniformly serialize `step`, `label`, and `timestamp` directly from `StepTransitionEvent`, and align all presentation-layer unit tests in `tests/api/test_sse_stream_adapter.py`.

---

## Detailed Requirements

1. **Refactor `SseStreamAdapter.format` (`app/api/streaming.py`)**:
   - Update signature to:
     ```python
     @classmethod
     async def format(
         cls,
         events: AsyncIterable[WorkflowExecutionEvent],
     ) -> AsyncGenerator[str, None]:
     ```
   - Delete `step_labels: dict[str, str] | None = None` parameter completely (Zero-Shim).
   - In the event dispatch loop, handle `StepTransitionEvent` uniformly:
     ```python
     if isinstance(event, StepTransitionEvent):
         yield format_sse(
             "step",
             {
                 "step": event.step,
                 "label": event.label,
                 "timestamp": event.timestamp,
             },
         )
     ```
   - Delete internal `datetime.now(UTC).timestamp()` computation inside `SseStreamAdapter.format` since timestamps are owned by domain events.

2. **Align Presentation Unit Tests (`tests/api/test_sse_stream_adapter.py`)**:
   - Update `test_adapter_step_transition_event`: Assert that the emitted frame data dictionary contains `step`, `label`, and `timestamp`.
   - Update `test_adapter_step_transition_with_step_labels_enrichment`: Rename to `test_adapter_step_transition_self_contained_event`, pass `StepTransitionEvent(step="retrieve_kb", label="Retrieving documents...")` directly into `SseStreamAdapter.format(event_generator())` without `step_labels`, and assert that the frame contains the provided label and timestamp.
   - Update `test_adapter_full_lifecycle_sequence`: Verify that step frames contain `step`, `label`, and `timestamp`.

---

## Verification Criteria

- `pytest tests/api/test_sse_stream_adapter.py` passes 100% green.
- `SseStreamAdapter.format` accepts only `events` and has zero references to `step_labels`.
- All `event: step` SSE frames emitted by `SseStreamAdapter` contain `step`, `label`, and `timestamp`.
