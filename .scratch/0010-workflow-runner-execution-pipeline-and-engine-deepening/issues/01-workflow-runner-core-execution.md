# 01: WorkflowRunner Core Execution & Unit Test Suite

**What to build:** A deep `WorkflowRunner` module that encapsulates workflow lookup, telemetry contextvars management, Langfuse callback integration, LangGraph graph execution, structured error fallback handling, and background evaluation scheduling behind `execute()` and `aexecute()` methods, fully verifiable via dedicated unit tests without HTTP overhead.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Create `app/rag/workflows/runner.py` defining `WorkflowRunner` with an injectable `WorkflowRegistry` constructor and module-level `get_workflow_runner()` factory.
- [ ] Implement `WorkflowRunner.execute(request: ChatRequest, background_tasks: BackgroundTasks | None = None) -> ChatResponse` and async equivalent `aexecute()`.
- [ ] Standardize trace ID sanitization, Langfuse callback handler creation, and structlog contextvar bindings (`workflow`, `trace_id`, `session_id`, `user_id`) in `execute()`.
- [ ] Handle graph execution errors by delegating to `workflow_def.handle_error(exc, initial_state)` to ensure graceful response degradation.
- [ ] Trigger online evaluation asynchronously via `BackgroundTasks` when provided, falling back to `asyncio.create_task`.
- [ ] Implement unit test suite in `tests/rag/test_workflow_runner.py` verifying synchronous execution, trace metadata propagation, error fallbacks, and evaluation scheduling against mock and registered workflows.
