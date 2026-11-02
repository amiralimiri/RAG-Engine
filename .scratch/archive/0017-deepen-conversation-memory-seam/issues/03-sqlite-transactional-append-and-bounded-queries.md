# Ticket 03: SQLite Adapter Transactional Append & SQL-Level Index Resolution

- **Milestone:** Deepen Conversation Memory Seam (ADR-0017)
- **Status:** completed
- **Blocked by:** Ticket 01 (Protocol Seam Deepening & No-Op Adapter)
- **Estimated Scope:** Medium

---

## 1. Goal & Context
Implement `aappend_turn` and bounded queries in `SQLiteConversationStore` (`app/rag/memory/sqlite.py`). Wrap rewind truncation and replacement turn persistence in a single SQLite transaction, resolve the next turn index in $O(1)$ via `SELECT COALESCE(MAX(turn_index) + 1, 0)`, and filter `turn_index < before_turn_index` directly in SQL queries.

---

## 2. Technical Requirements

### 2.1 `SQLiteConversationStore` Implementation (`app/rag/memory/sqlite.py`)
- Implement `aappend_turn`:
  - Acquire `self._write_lock`.
  - Inside a threadpool-dispatched sync helper with `with self._connection() as conn: with conn:` (single SQLite transaction):
    - If `rewind_turn_index is not None`:
      - Execute `DELETE FROM conversation_turns WHERE session_id = ? AND turn_index >= ?;`
      - Assign `turn_index = rewind_turn_index`.
    - If `rewind_turn_index is None`:
      - Query `SELECT COALESCE(MAX(turn_index) + 1, 0) AS next_idx FROM conversation_turns WHERE session_id = ?;`
      - Assign `turn_index = int(cursor.fetchone()["next_idx"])`.
    - Upsert `conversation_sessions` setting `title = user_query[:60]` and `updated_at = now_iso`.
    - Insert new row into `conversation_turns` with serialized `attachments_json` and `citations_json`.
    - Return reconstructed `ConversationTurn`.
- Update `aget_turns`:
  - Accept `before_turn_index: int | None = None`.
  - If `before_turn_index is not None`: add `AND turn_index < ?` parameter and predicate to the `WHERE` clause.
  - Preserve `ORDER BY turn_index DESC LIMIT ?` inner query when `limit` is provided, followed by outer `ORDER BY turn_index ASC`.
- Update `aget_messages`:
  - Accept `before_turn_index: int | None = None` and pass through to `aget_turns(session_id, limit=limit, before_turn_index=before_turn_index)`.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- In `tests/rag/test_conversation_memory_sqlite.py`:
  - Test sequential `aappend_turn` calls assign monotonic indices (`0, 1, 2`) with sub-millisecond execution.
  - Test `aappend_turn` with `rewind_turn_index=1` atomically prunes turns $\ge 1$ and saves replacement turn at index `1`.
  - Test `aget_turns` and `aget_messages` with `before_turn_index=2` and `limit=1` returns only turn index `1`.
  - Test concurrent calls to `aappend_turn` on the same session execute safely without constraint collisions.
  - Test rollbacks: if an insert error occurs during rewind append, previous turns are not permanently lost.

### Green (Implementation)
- Implement changes in `app/rag/memory/sqlite.py`.

---

## Acceptance Criteria
- [x] `aappend_turn` executes rewind deletion and new turn insertion within a single atomic SQLite transaction.
- [x] Non-rewind appends calculate `turn_index` via SQL `MAX(turn_index) + 1` with zero JSON deserialization.
- [x] `aget_turns` and `aget_messages` filter `turn_index < before_turn_index` in SQL.
- [x] All tests in `tests/rag/test_conversation_memory_sqlite.py` pass cleanly.
