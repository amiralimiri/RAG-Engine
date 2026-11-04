# Ticket 01: BoundExecutionContext Domain Model & Base Exports

- **Part of:** ADR-0021 (`docs/specs/0021-extract-execution-context-binder-seam-spec.md`)
- **Blockers:** None
- **Scope:** `app/rag/workflows/base.py`, `app/rag/workflows/__init__.py`
- **Labels:** `ready-for-agent`

---

## Objective

Define the frozen domain value object `BoundExecutionContext` in `app/rag/workflows/base.py` and export it canonically from `app/rag/workflows/__init__.py`.

---

## Detailed Requirements

1. **Implement `BoundExecutionContext`**:
   - Model class inherits from `pydantic.BaseModel`.
   - `model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)`.
   - Fields:
     - `workflow: WorkflowDefinition`: The resolved workflow definition.
     - `initial_state: dict[str, Any]`: The converted initial graph state.
     - `config: dict[str, Any]`: Pre-merged LangGraph RunnableConfig containing `callbacks`, `tags`, and `metadata`.
     - `trace_id: str`: The unique execution trace ID.
     - `session_id: str | None = None`: Session correlation ID.
     - `variant_id: str | None = None`: Resolved execution profile or variant ID.
     - `resolved_parameters: dict[str, Any] = Field(default_factory=dict)`: Evaluated parameter dictionary.
     - `hydrated_messages: list[BaseMessage] | None = None`: Hydrated conversational message objects.
   - Helper Method:
     - `def as_runnable_config(self) -> dict[str, Any]`: Returns a defensive shallow copy of `config`.

2. **Base Exports**:
   - Add `BoundExecutionContext` to `__all__` in `app/rag/workflows/base.py` and `app/rag/workflows/__init__.py`.

---

## Verification Criteria

- Instantiating `BoundExecutionContext` works with valid fields.
- Mutations raise `ValidationError` or `AttributeError` due to `frozen=True`.
- `as_runnable_config()` returns a valid dictionary.
