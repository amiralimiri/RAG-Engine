# Ticket 03: Durable SQLite Persistence Adapter with WAL & Lineage Tracking

- **Milestone:** Unified Pluggable Conversation Memory (ADR-0016)
- **Status:** Pending
- **Dependency:** Ticket 01, Ticket 02
- **Estimated Scope:** Bite-sized

---

## 1. Goal & Context
Implement `SQLiteConversationStore` (`app/rag/memory/sqlite.py`) satisfying `ConversationMemoryAdapter`:
1. Use standard library `sqlite3` with zero external pip dependencies.
2. Ensure persistence across server restarts so returning users retain conversation history.
3. Configure Write-Ahead Logging (`PRAGMA journal_mode=WAL;`), busy timeout (`5000ms`), and foreign key enforcement (`PRAGMA foreign_keys=ON;`).
4. Dispatch database calls via Starlette's `run_in_threadpool` and guard write operations with an `asyncio.Lock`.
5. Implement relational schema: `conversation_sessions` and `conversation_turns` with cascading deletes.

---

## 2. Technical Requirements

### 2.1 Schema Initialization & Invariants (`app/rag/memory/sqlite.py`)
- Tables and Indexes:
  ```sql
  CREATE TABLE IF NOT EXISTS conversation_sessions (
      session_id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      parent_session_id TEXT NULL REFERENCES conversation_sessions(session_id) ON DELETE SET NULL,
      forked_from_turn INTEGER NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS conversation_turns (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id) ON DELETE CASCADE,
      turn_index INTEGER NOT NULL,
      user_query TEXT NOT NULL,
      assistant_answer TEXT NOT NULL,
      attachments_json TEXT NULL,
      citations_json TEXT NULL,
      source_used TEXT NOT NULL,
      trace_id TEXT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(session_id, turn_index)
  );

  CREATE INDEX IF NOT EXISTS idx_turns_session_idx ON conversation_turns(session_id, turn_index);
  CREATE INDEX IF NOT EXISTS idx_sessions_updated ON conversation_sessions(updated_at DESC);
  ```

### 2.2 Methods Implementation
- `aget_messages(session_id, limit=None) -> list[BaseMessage]`: Fetch turns, convert via `to_messages()`.
- `aget_turns(session_id, limit=None) -> list[ConversationTurn]`:
  - If `limit` is provided, select the latest $N$ turns ordered chronologically (`turn_index ASC`).
  - Deserialize `attachments_json` and `citations_json`. Parse ISO timestamps.
- `asave_turn(turn: ConversationTurn) -> None`:
  - Ensure parent session exists in `conversation_sessions` (upsert session record with title from `turn.user_query[:60].strip()`).
  - Insert or replace turn into `conversation_turns`.
  - Update `updated_at` on `conversation_sessions`.
- `aclear(session_id: str) -> None`:
  - `DELETE FROM conversation_sessions WHERE session_id = ?` (cascades to turns via SQLite foreign key).
- `atruncate_from(session_id: str, turn_index: int) -> None`:
  - `DELETE FROM conversation_turns WHERE session_id = ? AND turn_index >= ?`.
  - Update session `updated_at`.
- `afork_session(source_session_id: str, upto_turn_index: int, new_session_id: str | None = None) -> str`:
  - Target ID: `new_session_id or uuid.uuid4().hex`.
  - Fetch source session title.
  - Insert new row into `conversation_sessions`: `session_id=new_session_id`, `title=f"{source_title} (Fork)"`, `parent_session_id=source_session_id`, `forked_from_turn=upto_turn_index`.
  - If `upto_turn_index >= 0`:
    - `INSERT INTO conversation_turns (session_id, turn_index, user_query, assistant_answer, attachments_json, citations_json, source_used, trace_id, created_at) SELECT ?, turn_index, user_query, assistant_answer, attachments_json, citations_json, source_used, trace_id, created_at FROM conversation_turns WHERE session_id = ? AND turn_index <= ? ORDER BY turn_index ASC`.
  - Return `new_session_id`.
- `alist_sessions(limit: int = 50) -> list[SessionInfo]`:
  - Query sessions joined with `COUNT(conversation_turns.id)` grouped by `session_id`, ordered by `updated_at DESC`.
- `aget_session(session_id: str) -> SessionInfo | None`:
  - Query session metadata and turn count. Return `None` if not found.

### 2.3 Factory Integration (`app/rag/memory/__init__.py`)
- Wire `SQLiteConversationStore` into `get_conversation_memory()` when `settings.conversation_memory_backend == "sqlite"`.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- Create `tests/rag/test_conversation_memory_sqlite.py`:
  - Use `tmp_path / "test_conversations.db"` fixture.
  - `test_sqlite_persistence_across_connections`: Persist a turn, close connection, re-instantiate `SQLiteConversationStore` against the same file, verify turn and session still exist.
  - `test_sqlite_save_and_get_turns_and_messages`: Verify JSON serialization of attachments and citations, and conversion to LangChain messages.
  - `test_sqlite_bounded_turns_retrieval`: Verify `limit` returns only the last $N$ turns in correct ascending chronological order.
  - `test_sqlite_truncate_from`: Truncate at turn $k$; verify turns $\ge k$ deleted.
  - `test_sqlite_fork_session`: Verify cloned turns and lineage tracking.
  - `test_sqlite_fork_turn_zero`: Verify `upto_turn_index = -1` creates empty child session with parent foreign key.
  - `test_sqlite_cascade_delete`: Verify `aclear` deletes parent session and all child turns.
  - `test_sqlite_list_sessions`: Verify `alist_sessions` returns sessions with correct `turn_count` ordered by `updated_at DESC`.

### Green (Implementation)
- Implement `app/rag/memory/sqlite.py`.
- Update `app/rag/memory/__init__.py` to import and instantiate `SQLiteConversationStore`.

### Refactor
- Ensure all queries use parameterized SQL (`?`) to prevent injection.
- Ensure threadpool dispatch wraps all database I/O.
