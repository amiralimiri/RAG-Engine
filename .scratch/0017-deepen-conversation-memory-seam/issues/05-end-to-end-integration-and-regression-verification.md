# Ticket 05: End-to-End Integration & Regression Verification

- **Milestone:** Deepen Conversation Memory Seam (ADR-0017)
- **Status:** completed
- **Blocked by:** Ticket 04 (WorkflowRunner Caller Streamlining)
- **Estimated Scope:** Small

---

## 1. Goal & Context
Perform comprehensive end-to-end integration and regression testing across HTTP endpoints (`/api/chat`, `/api/chat/stream`, `/api/chat/{session_id}/rewind`) and the full unit test suite. Verify that all 651+ tests pass, that standalone REST rewinds continue to function via `atruncate_from`, and that multi-turn conversational chat exhibits zero regressions or performance degradation.

---

## 2. Technical Requirements

### 2.1 API Route Verification (`tests/api/test_routes_chat_memory.py`)
- Verify that `POST /api/chat` with sequential turns persists monotonically incrementing turns.
- Verify that `POST /api/chat` with `rewind_turn_index` truncates obsolete turns and persists replacement turn via `aappend_turn`.
- Verify that standalone `POST /api/chat/{session_id}/rewind` continues to work with `atruncate_from`.

### 2.2 Full Regression Suite
- Run `uv run pytest tests` to verify all test suites pass without warnings or errors.
- Confirm that no test doubles or mocks break due to the protocol additions.

---

## 3. Implementation Steps
- Run `tests/api/test_routes_chat_memory.py` and `tests/test_workflow_engine_memory.py`.
- Run full pytest suite: `uv run pytest tests`.
- Verify zero lingering deprecated method calls in the persistence read and write paths.

---

## Acceptance Criteria
- [x] End-to-end multi-turn conversation and rewind flows pass via HTTP routes.
- [x] Explicit REST rewind endpoint (`/api/chat/{session_id}/rewind`) functions properly.
- [x] Entire test suite (`uv run pytest tests`) passes 100% green (651+ tests).
- [x] No regressions in streaming or synchronous execution pipelines.
