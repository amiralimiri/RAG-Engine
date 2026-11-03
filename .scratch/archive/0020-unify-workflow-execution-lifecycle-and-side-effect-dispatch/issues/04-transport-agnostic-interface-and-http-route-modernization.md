# 04: Transport-Agnostic Interface & HTTP Route Modernization

**What to build:** Remove framework-specific `BackgroundTasks` parameters from the `WorkflowRunner` execution interface (`execute`, `execute_with_context`, and `aexecute`), and modernize the `POST /api/chat` FastAPI route signature to a clean, declarative endpoint. Run full regression tests across memory persistence, online evaluation, and streaming routes.

**Blocked by:** 03: Prune Private Bypass Methods & Mutable Property Setters

**Status:** closed

- [x] Remove `background_tasks: BackgroundTasks | None = None` from `WorkflowRunner.execute`, `WorkflowRunner.execute_with_context`, and `WorkflowRunner.aexecute`.
- [x] Remove `from fastapi import BackgroundTasks` from `app/rag/workflows/runner.py`.
- [x] Modernize `chat()` in `app/api/routes.py` to `def chat(payload: ChatRequest):` without `background_tasks: BackgroundTasks`.
- [x] Verify that `POST /api/chat` persists conversation turns and triggers background online evaluation via `OnlineEvaluationListener` without requiring `BackgroundTasks`.
- [x] Run full test suite regression (`pytest`) verifying memory hydration, in-place rewind, streaming event generation, and evaluation scoring.
