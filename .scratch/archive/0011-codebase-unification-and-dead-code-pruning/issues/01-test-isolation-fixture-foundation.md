# 01: Test Isolation Fixture Foundation

**What to build:** Establish a centralized `tests/conftest.py` with an autouse `clean_registry` fixture that resets `WorkflowRegistry` to canonical defaults before and after each test, and provide a standardized `register_mock_workflow` fixture and `MockWorkflowDefinition` class to allow all subsequent test refactorings to register test doubles cleanly without monkeypatching module dictionaries.

**Blocked by:** None

**Status:** closed

---

## Tasks

- [x] Create `tests/conftest.py`.
- [x] Define `MockWorkflowDefinition` satisfying `WorkflowDefinition` protocol:
  - Configurable `info` (`WorkflowInfo` with step labels and generation nodes).
  - Configurable `answer`, `source_used`, `trace`, `citations`, `retrieved_contexts`.
  - Configurable `_custom_graph` or synthetic mock supporting `invoke` and `astream_events` v2.
  - State mapping methods: `to_initial_state`, `to_chat_response`, `extract_eval_context`, `handle_error`.
- [x] Implement `clean_registry` fixture with `autouse=True`:
  - Fetch `WorkflowRegistry` via `get_workflow_registry()`.
  - Call `registry.clear()` and `register_default_workflows(registry)` on setup.
  - Yield registry to tests.
  - Call `registry.clear()` and `register_default_workflows(registry)` on teardown.
- [x] Implement `register_mock_workflow` fixture helper returning a callable `(workflow_id: str, **kwargs) -> MockWorkflowDefinition` that registers the double into `clean_registry`.
- [x] Write unit tests inside `tests/unit/test_conftest.py` (or verify via existing tests) confirming `clean_registry` executes on every test and isolates mutations.

## Verification
- Run `uv run pytest tests/rag/test_registry.py` to confirm standard registry operations pass under the new fixture.
