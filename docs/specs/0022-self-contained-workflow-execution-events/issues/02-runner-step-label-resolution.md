# Ticket 02: Runner Step Label Resolution at Emission Seam

- **Part of:** ADR-0022 (`docs/specs/0022-self-contained-workflow-execution-events-spec.md`)
- **Blockers:** Ticket 01
- **Scope:** `app/rag/workflows/runner.py`, `tests/rag/test_workflow_runner.py`
- **Labels:** `ready-for-agent`

---

## Objective

Update `WorkflowRunner._stream_generator` in `app/rag/workflows/runner.py` to resolve human-readable labels from the bound workflow definition's `step_labels` (with fallback to `DEFAULT_STEP_LABELS` or `node_name`) when emitting `StepTransitionEvent`, establishing the runner as the single authority for label resolution.

---

## Detailed Requirements

1. **Update `WorkflowRunner._stream_generator` (`app/rag/workflows/runner.py`)**:
   - Locate the step transition emission logic:
     ```python
     if stream_steps and ev_type == "on_chain_start" and node_name in step_labels:
         yield StepTransitionEvent(step=node_name)
     ```
   - Update it to resolve the label from `step_labels`:
     ```python
     if stream_steps and ev_type == "on_chain_start" and node_name in step_labels:
         resolved_label = step_labels.get(node_name, node_name)
         yield StepTransitionEvent(step=node_name, label=resolved_label)
     ```
   - Ensure `step_labels` defaults properly to `DEFAULT_STEP_LABELS` when `wf_def.info.step_labels` is absent or empty.

2. **Unit Tests (`tests/rag/test_workflow_runner.py`)**:
   - Add/update unit tests asserting that `WorkflowRunner.stream` yields `StepTransitionEvent` instances whose `.label` attribute matches the expected description from `DEFAULT_STEP_LABELS` or custom workflow `step_labels`.
   - Verify that custom workflow definitions with custom `step_labels` correctly propagate their custom labels onto emitted `StepTransitionEvent` instances.

---

## Verification Criteria

- `pytest tests/rag/test_workflow_runner.py` passes cleanly.
- Emitted `StepTransitionEvent` instances from `WorkflowRunner.stream` always have `.label` matching the resolved workflow step label rather than falling back to the raw node name.
