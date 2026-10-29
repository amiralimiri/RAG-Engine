# 05: HTTP Router Collapse & Full E2E Streaming Regression

**What to build:** Collapse `app/api/routes.py` from 745 lines into a lean transport adapter under 120 lines by delegating `/api/chat` and `/api/chat/stream` directly to `WorkflowRunner`, re-exporting legacy test dictionaries from `app.rag.workflows.legacy`, and passing the full regression test suite.

**Blocked by:** 02: WorkflowRunner SSE Streaming & Tool Delta Filtering, 03: Legacy Compatibility Facade & Test-Shim Isolation, 04: Engine Pruning & Legacy Entrypoint Modernization

**Status:** completed

- [x] Refactor `app/api/routes.py` to remove `chat_event_generator`, `format_sse`, `is_tool_call_chunk`, `_LegacyUnifiedWorkflowAdapter`, `_sync_legacy_registry`, `_LegacyAsksDict`, and `_LegacyGraphsDict`.
- [x] Re-export `WORKFLOW_ASKS` and `WORKFLOW_GRAPHS` on `routes` from `app.rag.workflows.legacy`.
- [x] Update `POST /api/chat` to delegate directly to `get_workflow_runner().execute(payload, background_tasks=background_tasks)`.
- [x] Update `POST /api/chat/stream` to return `StreamingResponse(get_workflow_runner().stream(payload), media_type="text/event-stream; charset=utf-8")`.
- [x] Verify that `app/api/routes.py` is under 120 lines of code.
- [x] Run full regression test suite (`tests/`) ensuring all 438+ tests pass with zero regressions.
