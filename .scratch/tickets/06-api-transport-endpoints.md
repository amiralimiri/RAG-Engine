# Ticket 06: REST API Schemas & Endpoints (Sessions List, History, Delete, Rewind, Fork)

- **Milestone:** Unified Pluggable Conversation Memory (ADR-0016)
- **Status:** Completed
- **Dependency:** Ticket 01, Ticket 02, Ticket 03, Ticket 05
- **Estimated Scope:** Bite-sized

---

## 1. Goal & Context
Expose conversation memory, session management, rewind, and branching capabilities over HTTP:
1. Define request and response schemas in `app/api/schemas.py`.
2. Add endpoints to `app/api/routes.py`:
   - `GET /api/chat/sessions` (list recent sessions)
   - `GET /api/chat/{session_id}` (rich session & turn history for resumption)
   - `DELETE /api/chat/{session_id}` (cascade deletion)
   - `POST /api/chat/{session_id}/rewind` (standalone truncation)
   - `POST /api/chat/{session_id}/fork` (branching with lineage tracking)
3. Add `rewind_turn_index` to `ChatRequest` for atomic edit-and-run requests.

---

## 2. Technical Requirements

### 2.1 API Schemas (`app/api/schemas.py`)
- `ChatTurnResponse`:
  - `turn_index: int`, `user_query: str`, `assistant_answer: str`, `attachments: list[dict]`, `citations: list[dict]`, `source_used: str`, `trace_id: str | None`, `created_at: datetime`.
- `SessionDetailResponse`:
  - `session_id: str`, `title: str`, `parent_session_id: str | None`, `forked_from_turn: int | None`, `created_at: datetime`, `updated_at: datetime`, `turn_count: int`, `turns: list[ChatTurnResponse]`.
- `SessionSummaryResponse` & `SessionListResponse`:
  - `sessions: list[SessionSummaryResponse]`, `total: int`.
- `RewindRequest`:
  - `turn_index: int = Field(..., ge=0)`
- `ForkRequest`:
  - `upto_turn_index: int = Field(..., ge=-1)`
  - `new_session_id: str | None = None`
- Update `ChatRequest`:
  - Add optional `rewind_turn_index: int | None = Field(default=None, ge=0)`.
  - Pass through `to_workflow_input()`.

### 2.2 Route Handlers (`app/api/routes.py`)
- Register `/api/chat/sessions` **before** `/api/chat/{session_id}` to prevent route collision.
- `GET /api/chat/sessions`:
  - Invokes `memory.alist_sessions(limit=50)`.
  - Returns `SessionListResponse`.
- `GET /api/chat/{session_id}`:
  - Invokes `memory.aget_session(session_id)` and `memory.aget_turns(session_id)`.
  - If session not found, raise `HTTPException(status_code=404, detail="Session not found")`.
  - Return `SessionDetailResponse`.
- `DELETE /api/chat/{session_id}`:
  - Invokes `memory.aclear(session_id)`.
  - Returns `{"status": "deleted", "session_id": session_id}`.
- `POST /api/chat/{session_id}/rewind`:
  - Validates session exists.
  - Invokes `memory.atruncate_from(session_id, payload.turn_index)`.
  - Returns `{"status": "rewound", "session_id": session_id, "turn_index": payload.turn_index}`.
- `POST /api/chat/{session_id}/fork`:
  - Validates session exists.
  - Invokes `memory.afork_session(session_id, payload.upto_turn_index, payload.new_session_id)`.
  - Fetches and returns `SessionDetailResponse` for the new session.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- Create `tests/api/test_routes_chat_memory.py`:
  - `test_get_sessions_empty`: Verify `GET /api/chat/sessions` returns 200 with empty list.
  - `test_get_session_not_found`: Verify `GET /api/chat/unknown` returns 404.
  - `test_save_turn_and_get_session_detail`: Execute `POST /api/chat`, then `GET /api/chat/{session_id}`, verify turn details and citations match.
  - `test_delete_session`: Create session, call `DELETE /api/chat/{session_id}`, verify subsequent `GET` returns 404.
  - `test_rewind_session_endpoint`: Create multi-turn session, call `POST /api/chat/{session_id}/rewind`, verify remaining turns.
  - `test_fork_session_endpoint`: Call `POST /api/chat/{session_id}/fork`, verify new session created with parent metadata and copied turns.
  - `test_fork_turn_zero_endpoint`: Call with `upto_turn_index = -1`, verify empty branched session with lineage.
  - `test_atomic_rewind_via_chat_post`: Call `POST /api/chat` with `rewind_turn_index`, verify atomic truncate and replacement.

### Green (Implementation)
- Update `app/api/schemas.py`.
- Update `app/api/routes.py`.

### Refactor
- Ensure error codes and exception messages are consistent.
