# Ticket 03: Integrate Binder into WorkflowRunner & Streamline

- **Part of:** ADR-0021 (`docs/specs/0021-extract-execution-context-binder-seam-spec.md`)
- **Blockers:** Ticket 02
- **Scope:** `app/rag/workflows/runner.py`, `tests/rag/test_workflow_runner.py`
- **Labels:** `ready-for-agent`

---

## Objective

Refactor `WorkflowRunner` to delegate all execution preparation to `ExecutionContextBinder`, prune obsolete internal hydration and preparation methods, and verify runner behavior.

---

## Detailed Requirements

1. **`WorkflowRunner` Constructor Refactoring**:
   - Accept `binder: ExecutionContextBinder | None = None`.
   - If `binder` is omitted, auto-wire `ExecutionContextBinder(registry=self.registry, memory=memory, callback_factory=callback_factory, trace_extractor=trace_extractor)`.
   - Expose read-only `binder` property.
   - Forward `runner.memory` to `self._binder.memory`.
   - Forward `runner.resolve_workflow(workflow_name)` to `self._binder.resolve_workflow(workflow_name)`.

2. **Streamline Execution Pathways**:
   - In `execute()`: Call `bound = self._binder.bind(wf_input, ...)`. Unpack `bound.workflow`, `bound.initial_state`, `bound.trace_id`, and `bound.as_runnable_config()`.
   - In `execute_with_context()`: Call `bound = self._binder.bind(wf_input, ...)`.
   - In `aexecute()`: Call `bound = await self._binder.abind(wf_input, ...)`.
   - In `stream()`: Call `bound = await self._binder.abind(wf_input, streaming=True, ...)`.

3. **Prune Obsolete Methods**:
   - Delete `_prepare_execution`, `_hydrate_memory_sync`, and `_ahydrate_memory` from `WorkflowRunner`.
   - Verify that `WorkflowRunner` file shrinks by ~250 lines.

4. **Runner Tests**:
   - Update `tests/rag/test_workflow_runner.py` if any private runner methods were referenced.
   - Add tests verifying custom binder injection in `WorkflowRunner(binder=custom_binder)`.

---

## Verification Criteria

- All tests in `tests/rag/test_workflow_runner.py` pass.
- `WorkflowRunner` delegates cleanly to `ExecutionContextBinder`.
