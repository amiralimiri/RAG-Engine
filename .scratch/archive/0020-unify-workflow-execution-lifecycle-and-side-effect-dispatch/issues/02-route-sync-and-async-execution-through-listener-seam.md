# 02: Route Synchronous and Asynchronous Execution Through the Listener Seam

**What to build:** Unify all execution pathways (`execute()`, `execute_with_context()`, `aexecute()`, and `stream()`) onto the centralized listener dispatch engine. Discontinue the execution bypass by invoking `_dispatch_listeners` and `_adispatch_listeners` on completion and on unhandled errors across sync and async execution.

**Blocked by:** 01: Unified Listener Dispatch Engine & Fail-Open Semantics in WorkflowRunner

**Status:** closed

- [x] Update `_finalize_execution` in `WorkflowRunner` to notify `on_completed` and `on_background_task` via `_dispatch_listeners("completed", request, domain_output)` instead of calling inline persistence and evaluation helpers.
- [x] Update `_afinalize_execution` in `WorkflowRunner` to notify `await self._adispatch_listeners("completed", request, domain_output)`.
- [x] Update `stream()` in `WorkflowRunner` to use `_adispatch_listeners("completed", ...)` and `_adispatch_listeners("cancelled", ...)`, eliminating inline listener loop duplication.
- [x] Add `on_error` listener notification to error blocks in `_execute_with_state`, `_aexecute_with_state`, and `_stream_generator` prior to error handling or re-raising.
- [x] Unit test that synchronous `execute()`, asynchronous `aexecute()`, and streaming `stream()` trigger identical `on_completed` and `on_background_task` listener events.
- [x] Unit test that graph execution errors trigger `on_error` across sync, async, and streaming routes.
