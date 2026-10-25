# Ticket 06: Update Chat API & Add Feedback Endpoint

## Status
Pending

## Dependencies
Ticket 02 (`02-telemetry-module.md`), Ticket 03 (`03-audit-sqlite-migration.md`), Ticket 04 (`04-workflow-instrumentation-fix-single-llm.md`), Ticket 05 (`05-workflow-instrumentation-fix-single-jev.md`)

## Scope
Update `app/api/routes.py` to:
1. Extend `ChatRequest` schema with optional `session_id` and `user_id`.
2. Construct Langfuse callback in `chat()`, pass it into `ask()`, extract `trace_id`, write audit record with `trace_id`, and return `trace_id` in response JSON.
3. Introduce `FeedbackRequest` schema and `POST /api/feedback` endpoint with asynchronous background execution via FastAPI `BackgroundTasks`.

## Deliverables
1. `app/api/routes.py`:
   - Updated `ChatRequest` model with `session_id: str | None = None`, `user_id: str | None = None`.
   - New `FeedbackRequest` model with `trace_id: str`, `score: float` (ge=0.0, le=1.0), `name: str = "user_feedback"`, `comment: str | None = None`.
   - Modified `@router.post("/chat")` route calling telemetry factory, `write_audit()`, and returning `trace_id`.
   - New `@router.post("/feedback")` route enqueuing `_submit_feedback_task` via `BackgroundTasks`.
2. `tests/test_feedback_api.py`: Integration tests for `/api/chat` trace return and `/api/feedback` handling.

## Acceptance Criteria
- [ ] `POST /api/chat` returns HTTP 200 with JSON containing `"trace_id"`.
- [ ] `POST /api/chat` passes `session_id`, `user_id`, and callback to the respective workflow `ask()` function.
- [ ] `POST /api/chat` records `trace_id` in `data/audit.db` via `write_audit()`.
- [ ] `POST /api/feedback` returns HTTP 200 with `{"status": "accepted", "trace_id": "<id>"}`.
- [ ] `POST /api/feedback` rejects invalid scores (< 0.0 or > 1.0) with HTTP 422 Unprocessable Entity.
- [ ] Feedback submission runs in a FastAPI background task without blocking the response.
- [ ] If Langfuse client fails in the background task, the error is logged without crashing the application.

## TDD Test Requirements
Create `tests/test_feedback_api.py`:
- `test_chat_endpoint_returns_trace_id(client, monkeypatch)`: Call `POST /api/chat` with mock ask function, verify `"trace_id"` is present in response dict.
- `test_chat_endpoint_stores_trace_in_audit(client, monkeypatch)`: Call `POST /api/chat`, verify `write_audit` received the matching `trace_id`.
- `test_feedback_endpoint_accepts_valid_payload(client, monkeypatch)`: Post valid feedback (`{"trace_id": "tr-123", "score": 1.0, "comment": "great"}`), assert HTTP 200 and accepted status.
- `test_feedback_endpoint_validates_score_bounds(client)`: Post score `1.5` and `-0.2`, assert HTTP 422 for both.
- `test_feedback_task_executes_langfuse_score(monkeypatch)`: Invoke `_submit_feedback_task` directly with mock client, verify `client.create_score` (or `client.score`) called with exact arguments.
