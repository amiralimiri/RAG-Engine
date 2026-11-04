# Ticket 04: Presentation Layer Integration & Regression Verification

- **ADR Reference:** [`docs/adr/0023-deepen-conversation-memory-session-aggregate.md`](../../../adr/0023-deepen-conversation-memory-session-aggregate.md)
- **Specification Reference:** [`docs/specs/0023-deepen-conversation-memory-session-aggregate-spec.md`](../0023-deepen-conversation-memory-session-aggregate-spec.md)
- **Target Files:**
  - `app/api/schemas.py`
  - `app/api/routes.py`
  - `tests/api/test_routes_chat_memory.py`
  - Full test suite regression
- **Blocked by:** Ticket 03
- **Status:** completed

---

## What to build

Update `SessionDetailResponse.from_domain` in `app/api/schemas.py` to support `ConversationSession` directly while retaining backward compatibility for `(SessionInfo, turns)`. In `app/api/routes.py`, simplify `GET /api/chat/{session_id}` to a single `aget_session` call and collapse `POST /api/chat/{session_id}/fork` to a single atomic `afork_session` call, handling `KeyError` as HTTP 404 and `ValueError` as HTTP 400. Align API tests in `tests/api/test_routes_chat_memory.py` and verify zero regressions across the entire test suite.

## Acceptance criteria

- [x] `SessionDetailResponse.from_domain` accepts `session: ConversationSession | SessionInfo, turns: list[ConversationTurn] | None = None` and properly serializes turns whether passed in `session.turns` or as a separate argument.
- [x] `GET /api/chat/{session_id}` in `app/api/routes.py` calls `await memory.aget_session(session_id)` once and converts directly to `SessionDetailResponse.from_domain(session)`.
- [x] `POST /api/chat/{session_id}/fork` in `app/api/routes.py` invokes `await memory.afork_session(...)` once without follow-up queries, catching `KeyError` to raise HTTP 404 and `ValueError` to raise HTTP 400.
- [x] Unit and route tests in `tests/api/test_routes_chat_memory.py` assert single-call execution and proper JSON response structure.
- [x] Full regression suite passes cleanly via `uv run pytest`.
