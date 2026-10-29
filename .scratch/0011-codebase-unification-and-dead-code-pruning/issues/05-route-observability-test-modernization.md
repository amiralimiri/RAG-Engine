# 05: Route & Observability Test Suite Modernization

**What to build:** Modernize all route, streaming, and observability tests that previously monkeypatched legacy dictionaries (`WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`) to use the centralized `clean_registry` fixture and `register_mock_workflow` helper (or `MockWorkflowDefinition`) introduced in Ticket 01.

**Blocked by:** 01: Test Isolation Fixture Foundation, 04: Legacy Workflow Shims & Dictionary Deletion

**Status:** open

---

## Tasks

- [ ] In `tests/test_chat_stream.py`:
  - Remove `from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS`.
  - Replace all `monkeypatch.setitem(WORKFLOW_GRAPHS, ...)` calls with registering mock workflows into `clean_registry` via `register_mock_workflow(workflow_id, graph=mock_graph)` or `clean_registry.register(MockWorkflowDefinition(...), override=True)`.
  - Replace `monkeypatch.setitem(WORKFLOW_ASKS, ...)` calls with mock workflow definitions.
- [ ] In `tests/test_frontend_streaming.py`:
  - Remove `from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS`.
  - Replace all monkeypatching on `WORKFLOW_ASKS` / `WORKFLOW_GRAPHS` with registering mock workflows into `clean_registry`.
- [ ] In `tests/test_e2e_observability.py`:
  - Remove `from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS`.
  - Replace dictionary monkeypatching with mock workflow registrations into `clean_registry`.
- [ ] In `tests/test_feedback_api.py`:
  - Remove `from app.api.routes import WORKFLOW_ASKS`.
  - Replace `monkeypatch.setitem(WORKFLOW_ASKS, ...)` with mock workflow registrations into `clean_registry`.
- [ ] In `tests/test_online_evaluation.py`:
  - Remove `from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS`.
  - Replace monkeypatching with mock workflow registrations into `clean_registry`.
- [ ] In `tests/unit/test_logging.py`:
  - Remove `from app.api.routes import WORKFLOW_ASKS`.
  - Replace `monkeypatch.setitem(WORKFLOW_ASKS, "fix_single_llm", mock_workflow_ask)` with registering a mock workflow into `clean_registry`.
- [ ] In `tests/test_frontend_integration.py`:
  - Remove `from app.api.routes import WORKFLOW_ASKS`.
  - Replace dictionary monkeypatching with mock workflow registration into `clean_registry`.

## Verification
- Run `uv run pytest tests/test_chat_stream.py tests/test_frontend_streaming.py tests/test_e2e_observability.py tests/test_feedback_api.py tests/test_online_evaluation.py tests/unit/test_logging.py tests/test_frontend_integration.py`.
