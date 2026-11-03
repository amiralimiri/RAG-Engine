# 01: Domain Event Subsystem & Schemas

**What to build:**
A strongly-typed, transport-agnostic domain event model representing the discrete execution milestones of any workflow execution (node transitions, token deltas, terminal completion, and execution failures) independent of HTTP or SSE wire protocols.

**Blocked by:**
None (can start immediately)

**Status:** closed

- [x] Create `app/rag/workflows/events.py` with typed Pydantic event models:
  - `StepTransitionEvent(type="step", step=str)`
  - `TokenDeltaEvent(type="token", delta=str)`
  - `ExecutionCompletedEvent(type="completed", output=WorkflowOutput)`
  - `ExecutionFailedEvent(type="failed", error=str, code=str)`
  - Discriminated union `WorkflowExecutionEvent = Union[StepTransitionEvent, TokenDeltaEvent, ExecutionCompletedEvent, ExecutionFailedEvent]`
- [x] Export events from `app/rag/workflows/__init__.py`.
- [x] Verify serialization, validation, and discriminated type matching in `tests/rag/test_workflow_events.py`.
