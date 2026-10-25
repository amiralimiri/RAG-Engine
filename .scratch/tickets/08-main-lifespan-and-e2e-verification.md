# Ticket 08: FastAPI Lifespan & End-to-End Test Suite

## Status
Completed

## Dependencies
Tickets 01 through 07

## Scope
Update `app/main.py` with FastAPI's async `lifespan` context manager to run startup database verification (`init_db()`) and ensure graceful shutdown telemetry flushing (`flush_telemetry()`). Build an end-to-end test suite confirming full pipeline operation with Langfuse enabled and disabled.

## Deliverables
1. `app/main.py`:
   - Replace manual global `init_db()` call with `@asynccontextmanager async def lifespan(app: FastAPI)` context manager.
   - Call `flush_telemetry()` on application shutdown.
   - Pass `lifespan=lifespan` to `FastAPI(..., lifespan=lifespan)`.
2. `tests/test_e2e_observability.py`: End-to-end integration test suite exercising the complete lifecycle across both workflows.

## Acceptance Criteria
- [x] Application starts up cleanly, executes `init_db()`, and runs `flush_telemetry()` on server shutdown.
- [x] Complete pipeline functions end-to-end when Langfuse is enabled (spans created, callbacks invoked, trace ID generated, feedback submitted).
- [x] Complete pipeline functions end-to-end when Langfuse is disabled or keys are unset (zero degradation, `trace_id` gracefully `None`).
- [x] Entire test suite (`pytest`) runs and passes with zero regressions across pre-existing tests.

## TDD Test Requirements
Create `tests/test_e2e_observability.py`:
- `test_lifespan_startup_and_shutdown(monkeypatch)`: Test FastAPI `TestClient` context manager; verify `init_db` is called on enter and `flush_telemetry` on exit.
- `test_e2e_flow_with_langfuse_mocked(client, monkeypatch)`: Mock Langfuse client and callback; invoke `/api/chat` with `fix_single_llm`, verify trace returned, invoke `/api/feedback` with that trace ID, assert 200 response and background score dispatch.
- `test_e2e_flow_with_langfuse_disabled(client, monkeypatch)`: Run chat invocation with `LANGFUSE_ENABLED=False`, verify normal response with `trace_id=None` and no exceptions.
