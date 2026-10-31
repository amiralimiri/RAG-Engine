# 01: Domain DTOs (`WorkflowInput`, `WorkflowOutput`) & Transport Decoupling

**What to build:**
Decouple `app/rag/workflows/` completely from the transport layer (`app.api.schemas`):
1. Define `WorkflowInput` and `WorkflowOutput` Pydantic models in `app/rag/workflows/base.py` with zero dependencies on `app.api.*`.
2. Update the `WorkflowDefinition` Protocol in `app/rag/workflows/base.py`:
   - `to_initial_state(input: WorkflowInput, trace_id: str | None = None) -> dict[str, Any]`
   - `to_workflow_output(final_state: dict[str, Any], trace_id: str | None = None) -> WorkflowOutput`
   (Keep a backward-compatibility facade method `to_chat_response` if needed, or update existing workflows directly).
3. Update `SelfCorrectiveRagWorkflow` and `ReActAgentWorkflow` to implement `to_initial_state(WorkflowInput)` and `to_workflow_output(final_state)` without importing `app.api.schemas`.
4. Update `WorkflowRunner` in `app/rag/workflows/runner.py` to bridge `ChatRequest` $\to$ `WorkflowInput` and `WorkflowOutput` $\to$ `ChatResponse`.
5. Update `tests/rag/test_workflow_contracts.py` and ensure existing tests run green.

**Blocked by:** None (can start immediately)

**Status:** open

- [ ] `WorkflowInput` and `WorkflowOutput` defined in `app/rag/workflows/base.py`
- [ ] No imports from `app.api.schemas` present in `app/rag/workflows/base.py`, `react_agent.py`, `self_corrective.py`
- [ ] `WorkflowDefinition` Protocol updated with domain DTO signatures
- [ ] `SelfCorrectiveRagWorkflow` and `ReActAgentWorkflow` implement `to_workflow_output`
- [ ] `WorkflowRunner` translates between transport models (`ChatRequest`/`ChatResponse`) and domain DTOs
- [ ] Conformance tests in `tests/rag/test_workflow_contracts.py` updated and passing
- [ ] All existing workflow and runner tests pass: `uv run pytest tests/rag/test_workflow_runner.py tests/rag/test_workflow_contracts.py`
