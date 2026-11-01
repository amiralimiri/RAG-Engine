# Ticket 02: In-Memory & NoOp Memory Adapters with Factory Entrypoint

- **Milestone:** Unified Pluggable Conversation Memory (ADR-0016)
- **Status:** Pending
- **Dependency:** Ticket 01
- **Estimated Scope:** Bite-sized

---

## 1. Goal & Context
Implement test double and stateless adapters satisfying `ConversationMemoryAdapter`:
1. `InMemoryConversationStore` (`app/rag/memory/in_memory.py`): In-memory, thread-safe implementation supporting all protocol operations (retrieval, save, truncate, clear, fork, list, session lookup) to serve as a fast double for unit tests.
2. `NoOpMemoryStore` (`app/rag/memory/noop.py`): Stateless pass-through adapter for disabling memory.
3. Factory module `app/rag/memory/__init__.py`: Provides `get_conversation_memory()` and `reset_conversation_memory()`.

---

## 2. Technical Requirements

### 2.1 `InMemoryConversationStore` (`app/rag/memory/in_memory.py`)
- Thread-safe using `threading.RLock()`.
- Internal storage:
  - `_sessions: dict[str, SessionInfo]`
  - `_turns: dict[str, list[ConversationTurn]]` (keyed by `session_id`, kept sorted by `turn_index`)
- Behavior:
  - `aget_messages(session_id, limit)`: Returns chronologically ordered messages. If `limit` is set, returns only the latest `limit` turns' messages.
  - `aget_turns(session_id, limit)`: Returns turn objects. If `limit` is set, returns the latest `limit` turns.
  - `asave_turn(turn)`:
    - Auto-creates `SessionInfo` if `session_id` does not exist (derives `title = turn.user_query[:60].strip()`).
    - Updates `SessionInfo.updated_at` and `turn_count`.
    - Upserts turn into `_turns[session_id]` based on `turn_index`.
  - `aclear(session_id)`: Removes session and all its turns.
  - `atruncate_from(session_id, turn_index)`: Deletes all turns with `turn_index >= turn_index`. Updates `SessionInfo.turn_count`.
  - `afork_session(source_session_id, upto_turn_index, new_session_id=None)`:
    - Clones turns `[0 .. upto_turn_index]` into `new_session_id` (or generated UUID).
    - If `upto_turn_index == -1`, copies 0 turns (Turn 0 branching).
    - Records `parent_session_id = source_session_id` and `forked_from_turn = upto_turn_index`.
  - `alist_sessions(limit)`: Returns sessions sorted by `updated_at` descending.
  - `aget_session(session_id)`: Returns `SessionInfo` or `None`.

### 2.2 `NoOpMemoryStore` (`app/rag/memory/noop.py`)
- Stateless no-op implementations:
  - `aget_messages`: returns `[]`
  - `aget_turns`: returns `[]`
  - `asave_turn`, `aclear`, `atruncate_from`: return `None`
  - `afork_session`: returns `new_session_id or uuid.uuid4().hex`
  - `alist_sessions`: returns `[]`
  - `aget_session`: returns `None`

### 2.3 Factory & Module Exports (`app/rag/memory/__init__.py`)
- `get_conversation_memory() -> ConversationMemoryAdapter`: Dynamically resolves adapter based on `settings.conversation_memory_backend`.
- `reset_conversation_memory() -> None`: Clears singleton for test isolation.
- Re-export `ConversationMemoryAdapter`, `ConversationTurn`, `SessionInfo`, `InMemoryConversationStore`, `NoOpMemoryStore`.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- Create `tests/rag/test_conversation_memory_in_memory.py`:
  - `test_in_memory_save_and_get_turns_and_messages`: Persist multiple turns, verify retrieval and ordering.
  - `test_in_memory_bounded_retrieval`: Persist 5 turns, query with `limit=2`, verify only turns 3 and 4 are returned.
  - `test_in_memory_truncate_from`: Truncate at index 2 of 4 turns; verify turns 0 and 1 remain, 2 and 3 removed.
  - `test_in_memory_fork_session`: Fork at turn 1; verify new session has turns 0 and 1, original session retains all turns, parent lineage is recorded.
  - `test_in_memory_fork_session_turn_zero`: Fork with `upto_turn_index = -1`; verify new session is created empty with `parent_session_id` set.
  - `test_in_memory_clear_session`: Verify `aclear` purges turns and session.
  - `test_noop_memory_store`: Verify all methods behave safely as no-ops.
  - `test_factory_resolution`: Verify `get_conversation_memory()` resolves based on settings.

### Green (Implementation)
- Implement `app/rag/memory/in_memory.py`.
- Implement `app/rag/memory/noop.py`.
- Implement `app/rag/memory/__init__.py`.

### Refactor
- Ensure thread-safety locks surround all mutable state access in `InMemoryConversationStore`.
