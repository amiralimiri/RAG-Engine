# 03: Legacy Compatibility Facade & Test-Shim Isolation

**What to build:** An isolated compatibility adapter module (`app/rag/workflows/legacy.py`) that encapsulates monkeypatched test dictionary shims and adapts legacy callable workflows into `WorkflowDefinition`, removing all test-shim machinery from HTTP routing.

**Blocked by:** 01: WorkflowRunner Core Execution & Unit Test Suite

**Status:** ready-for-agent

- [ ] Create `app/rag/workflows/legacy.py` containing `_LegacyUnifiedWorkflowAdapter`, `_sync_legacy_registry`, `_LegacyAsksDict`, and `_LegacyGraphsDict`.
- [ ] Export `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` from `app/rag/workflows/legacy.py` and re-export via `app/rag/workflows/__init__.py`.
- [ ] Ensure that modifications or deletions on `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` automatically synchronize with `WorkflowRegistry` via `_sync_legacy_registry`.
- [ ] Write dedicated conformance unit tests in `tests/rag/test_legacy_compat.py` verifying that dictionary mutations update the registry, that monkeypatched ask callables invoke cleanly, and that deleting entries resets defaults.
