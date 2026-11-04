# Ticket 04: Collapse Presentation Route & Regression Verification

- **Part of:** ADR-0022 (`docs/specs/0022-self-contained-workflow-execution-events-spec.md`)
- **Blockers:** Ticket 03
- **Scope:** `app/api/routes.py`, `tests/test_chat_stream.py`, and full regression suite
- **Labels:** `ready-for-agent`

---

## Objective

Collapse `POST /api/chat/stream` in `app/api/routes.py` by purging its 19-line workflow lookup and exception-handling block into a clean, 2-line streaming pipeline, update streaming tests in `tests/test_chat_stream.py`, and verify the entire test suite against regressions.

---

## Detailed Requirements

1. **Refactor `chat_stream` in `app/api/routes.py`**:
   - Delete the workflow lookup, registry resolution, and step label fallback logic:
     ```python
     # DELETE lines 75-84:
     # step_labels = None
     # if wf_input.workflow:
     #     ...
     ```
   - Streamline `chat_stream` to:
     ```python
     @router.post("/chat/stream")
     async def chat_stream(payload: ChatRequest):
         wf_input = payload.to_workflow_input()
         runner = get_workflow_runner()
         return StreamingResponse(
             SseStreamAdapter.format(runner.stream(wf_input)),
             media_type="text/event-stream; charset=utf-8",
             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
         )
     ```
   - Remove unused imports (such as `DEFAULT_STEP_LABELS` if no longer used in `routes.py`).

2. **Align Integration & Streaming Tests (`tests/test_chat_stream.py`)**:
   - Verify that all streaming tests asserting on `event: step` frames confirm the presence of `step`, `label`, and `timestamp`.
   - Ensure tests checking `stream_steps=False` still confirm step suppression.
   - Ensure client disconnect and error frame handling remain completely unaffected.

3. **Full Regression Verification**:
   - Run the complete test suite:
     `pytest tests/`
   - Verify zero regressions across all unit, API, RAG, and streaming tests.

---

## Verification Criteria

- `POST /api/chat/stream` contains zero workflow registry or step label references.
- `pytest tests/test_chat_stream.py` passes 100% green.
- Full pytest test suite (`pytest`) passes with 0 failures and 0 errors.
