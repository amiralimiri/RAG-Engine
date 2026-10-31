# 01: WorkflowRunner Constructor Auto-Wiring, Interface Shrinkage & Singleton Reset

**What to build:**
Deepen `WorkflowRunner` (`app/rag/workflows/runner.py`) by binding production telemetry and evaluation hooks as constructor defaults, pruning hook arguments from `execute()`, `aexecute()`, and `stream()`, internalizing state execution methods, and establishing an autouse singleton reset fixture in `tests/conftest.py`.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

### Acceptance Criteria
- [ ] `WorkflowRunner.__init__` defaults its hook attributes directly to canonical adapters:
  - `callback_factory` -> `get_langfuse_callback`
  - `trace_extractor` -> `extract_trace_id`
  - `telemetry_flusher` -> `flush_telemetry`
  - `eval_task_fn` -> `run_online_evaluation_task`
- [ ] `execute()` and `aexecute()` accept only `(request: ChatRequest, background_tasks: BackgroundTasks | None = None, **kwargs: Any) -> ChatResponse`, delegating internally to private state helpers.
- [ ] `stream()` accepts only `(request: ChatRequest) -> AsyncGenerator[str, None]`.
- [ ] `execute_with_state` and `aexecute_with_state` are internalized as private helpers `_execute_with_state` and `_aexecute_with_state`.
- [ ] Background evaluation in `_finalize_execution` and `_stream_generator` is shielded with try/except and structured logging so evaluation failures never propagate to user chat responses or abort streams.
- [ ] `reset_workflow_runner()` function is exported from `app/rag/workflows/runner.py` to reset `_default_runner` to `None`.
- [ ] An autouse fixture `clean_runner` is added to `tests/conftest.py` executing `reset_workflow_runner()` before and after every test.
