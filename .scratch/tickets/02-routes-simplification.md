# 02: Transport Layer Simplification (`app/api/routes.py`)

**What to build:**
Refactor `app/api/routes.py` to remove the `_runner_hooks()` helper and streamline `chat` and `chat_stream` endpoints into pure 1-line callers delegating directly to `get_workflow_runner()`.

**Blocked by:** 01: WorkflowRunner Constructor Auto-Wiring, Interface Shrinkage & Singleton Reset

**Status:** ready-for-agent

### Acceptance Criteria
- [ ] `_runner_hooks()` is permanently deleted from `app/api/routes.py`.
- [ ] `POST /api/chat` invokes `get_workflow_runner().execute(payload, background_tasks=background_tasks)` without passing unpacked hook dictionaries.
- [ ] `POST /api/chat/stream` invokes `get_workflow_runner().stream(payload)` without passing unpacked hook dictionaries.
- [ ] Route-level multi-workflow tests in `tests/api/test_routes_multi_workflow.py` and `tests/test_chat_stream.py` continue to pass with 100% green status.
