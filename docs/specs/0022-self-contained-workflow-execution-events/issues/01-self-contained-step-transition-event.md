# Ticket 01: Self-Contained StepTransitionEvent & Domain Event Tests

- **Part of:** ADR-0022 (`docs/specs/0022-self-contained-workflow-execution-events-spec.md`)
- **Blockers:** None
- **Scope:** `app/rag/workflows/events.py`, `tests/rag/test_workflow_events.py`
- **Labels:** `ready-for-agent`

---

## Objective

Enrich `StepTransitionEvent` in `app/rag/workflows/events.py` with `label` and `timestamp` fields, backed by a model validator that automatically defaults empty or omitted labels to `step`, and add deterministic unit tests in `tests/rag/test_workflow_events.py`.

---

## Detailed Requirements

1. **Enhance `StepTransitionEvent` (`app/rag/workflows/events.py`)**:
   - Add imports for `UTC`, `datetime`, and `Self` from typing.
   - Add `label: str = Field(default="", description="Human-readable progression label")`.
   - Add `timestamp: float = Field(default_factory=lambda: datetime.now(UTC).timestamp(), description="Chronological UTC milestone timestamp in seconds")`.
   - Implement `@model_validator(mode="after")` named `_ensure_label(self) -> Self` that checks `if not self.label or not self.label.strip(): self.label = self.step`.

2. **Unit Tests (`tests/rag/test_workflow_events.py`)**:
   - Verify `StepTransitionEvent(step="retrieve")`:
     - Has `event.step == "retrieve"`.
     - Has `event.label == "retrieve"` (validator fallback).
     - Has `event.timestamp` as a positive float close to current UTC timestamp.
   - Verify `StepTransitionEvent(step="retrieve", label="Searching internal KB...")`:
     - Preserves explicit `label == "Searching internal KB..."`.
   - Verify `StepTransitionEvent(step="retrieve", label="   ")`:
     - Falls back to `label == "retrieve"`.
   - Verify discriminated union deserialization via `WorkflowExecutionEvent` correctly parses `{"type": "step", "step": "node_a"}` into `StepTransitionEvent` with populated `label` and `timestamp`.

---

## Verification Criteria

- `pytest tests/rag/test_workflow_events.py` passes with 100% green status.
- Direct instantiation and dict parsing of `StepTransitionEvent` always yield non-empty `label` and valid `timestamp`.
