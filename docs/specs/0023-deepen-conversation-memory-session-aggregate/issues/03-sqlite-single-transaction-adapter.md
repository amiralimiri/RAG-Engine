# Ticket 03: SQLiteConversationStore Single-Transaction Implementation

- **ADR Reference:** [`docs/adr/0023-deepen-conversation-memory-session-aggregate.md`](../../../adr/0023-deepen-conversation-memory-session-aggregate.md)
- **Specification Reference:** [`docs/specs/0023-deepen-conversation-memory-session-aggregate-spec.md`](../0023-deepen-conversation-memory-session-aggregate-spec.md)
- **Target Files:**
  - `app/rag/memory/sqlite.py`
  - `tests/rag/test_conversation_memory_sqlite.py`
- **Blocked by:** Ticket 02
- **Status:** closed

---

## What to build

Implement single-transaction session retrieval and atomic branching in `SQLiteConversationStore`. In `aget_session`, query both session metadata and turns within one threadpool connection block; apply tail-window querying (`ORDER BY turn_index DESC LIMIT ?` reversed in Python) when `limit` is set. In `afork_session`, validate preconditions (raise `ValueError` for `upto_turn_index < -1`, raise `KeyError` if `source_session_id` does not exist), execute fork within `self._write_lock` and a single SQLite transaction, and return the complete `ConversationSession` aggregate.

## Acceptance criteria

- [x] `SQLiteConversationStore.aget_session(session_id, limit=None)` retrieves metadata and turns in a single threadpool execution and returns `ConversationSession | None`.
- [x] If `limit` is specified in `aget_session`, SQL queries `ORDER BY turn_index DESC LIMIT ?` and reverses the rows in Python to guarantee ascending chronological order (`turn_index ASC`).
- [x] If `limit is None` in `aget_session`, SQL queries `ORDER BY turn_index ASC`.
- [x] Non-existent session IDs passed to `aget_session` safely return `None`.
- [x] `SQLiteConversationStore.afork_session` validates that `source_session_id` exists in `conversation_sessions`, raising `KeyError` if missing.
- [x] `SQLiteConversationStore.afork_session` validates `upto_turn_index >= -1`, raising `ValueError` if less than `-1`.
- [x] `SQLiteConversationStore.afork_session` returns `ConversationSession` directly from the fork write transaction without subsequent read calls.
- [x] `tests/rag/test_conversation_memory_sqlite.py` validates single-transaction session queries, tail-windowing, atomic fork aggregate return, and invariant enforcement.
