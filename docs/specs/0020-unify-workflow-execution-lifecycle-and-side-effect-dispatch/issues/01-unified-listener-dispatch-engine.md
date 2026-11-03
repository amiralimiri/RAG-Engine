# 01: Unified Listener Dispatch Engine & Fail-Open Semantics in WorkflowRunner

**What to build:** A centralized dispatch mechanism (`_adispatch_listeners` and `_dispatch_listeners` via `run_async_safely`) within `WorkflowRunner` that coordinates lifecycle events (`on_completed`, `on_background_task`, `on_error`, `on_cancelled`) across all registered listeners with fail-open fault isolation, background task tracking to prevent garbage collection, and pre-notification enrichment of `retrieved_contexts`.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Implement `_adispatch_listeners(event_type, request, output=None, error=None)` in `WorkflowRunner` to sequentially await `on_completed`, `on_error`, or `on_cancelled` on all registered listeners.
- [ ] Implement `_dispatch_listeners(...)` delegating to `run_async_safely` for safe invocation in synchronous contexts without event loop collision.
- [ ] Ensure `on_background_task` is scheduled asynchronously via `asyncio.create_task` with GC-shielding sets (`_background_tasks`) upon successful completion.
- [ ] Wrap individual listener executions in try/except blocks to log warnings upon failure and preserve fail-open execution invariants.
- [ ] Ensure `output.retrieved_contexts` is normalized and bound to `WorkflowOutput` prior to listener notification.
- [ ] Unit test `_adispatch_listeners` and `_dispatch_listeners` using spy and failing listener test doubles.
