# 04: Legacy Workflow Shims & Dictionary Deletion

**What to build:** Permanently delete `app/rag/workflows/legacy.py` and standalone workflow shims `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`, clean up re-exports in `app/api/routes.py` and `app/rag/workflows/__init__.py`, simplify `WorkflowRunner._resolve_workflow` to be fully open-closed, modernize `evaluation/run_experiment.py` to benchmark workflows dynamically through `WorkflowRunner` / `WorkflowRegistry`, and delete `tests/rag/test_legacy_compat.py`.

**Blocked by:** 01: Test Isolation Fixture Foundation, 03: Pure Engine Factory & Tool Decoupling

**Status:** open

---

## Tasks

- [ ] Delete `app/rag/workflows/legacy.py`.
- [ ] In `app/rag/workflows/__init__.py`:
  - Remove imports of `WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, and `reset_legacy_defaults`.
  - Remove `sys.modules.get("app.rag.fix_single_llm")` and `sys.modules.get("app.rag.fix_single_jev")` reflection in `register_default_workflows()`.
  - Instantiate `workflow_llm` and `workflow_jev` purely from `SelfCorrectiveRagWorkflow`.
  - Prune `WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, and `reset_legacy_defaults` from `__all__`.
- [ ] In `app/api/routes.py`:
  - Remove `from app.rag.workflows.legacy import WORKFLOW_ASKS, WORKFLOW_GRAPHS`.
- [ ] Delete `app/rag/fix_single_llm.py`.
- [ ] Delete `app/rag/fix_single_jev.py`.
- [ ] Delete `tests/rag/test_legacy_compat.py`.
- [ ] In `app/rag/workflows/runner.py`:
  - Simplify `_resolve_workflow(self, workflow_name: str) -> WorkflowDefinition`:
    - If `len(self.registry) == 0`: call `register_default_workflows(self.registry)`.
    - Retrieve via `self.registry.get(workflow_name)`.
    - If `KeyError`, raise `HTTPException(status_code=400, detail=f"Unknown workflow: {workflow_name}")`.
    - Remove hardcoded tuple check `("fix_single_llm", "fix_single_jev", "react_agent")`.
- [ ] In `evaluation/run_experiment.py`:
  - Remove `from app.rag import fix_single_jev, fix_single_llm`.
  - Refactor `run_workflow_benchmark` to resolve the workflow via `get_workflow_runner().registry.get(workflow_name)` and execute queries via `runner.execute_with_state(ChatRequest(question=question, workflow=workflow_name))`.
  - Dynamically populate available workflows from `get_workflow_registry().list_ids()`.
- [ ] Verify `evaluation/run_experiment.py --help` runs cleanly without import errors.

## Verification
- Run `python -c "import app.main; import app.rag.workflows; import evaluation.run_experiment"`.
- Run `uv run pytest tests/rag/test_workflow_runner.py tests/rag/test_registry.py tests/test_experiment_runner.py`.
