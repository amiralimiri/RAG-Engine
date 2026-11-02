# Technical Specification: Deepen Conversation Memory Seam with Atomic Append and Bounded Message Hydration

- **ADR Reference:** `docs/adr/0017-deepen-conversation-memory-seam.md`
- **Target Subsystem:** `app/rag/memory/` and `app/rag/workflows/runner.py`
- **Specification Document:** `docs/spec.md` & `docs/specs/0017-deepen-conversation-memory-seam-spec.md`
- **Status:** Proposed

---

## Problem Statement

Following the introduction of the pluggable conversation memory subsystem (ADR-0016), multi-turn conversation history, session resumption, turn rewind, and session branching were established across all workflow paradigms.

However, the `ConversationMemoryAdapter` protocol seam remains shallow, leaking internal storage mechanics and serialization burdens onto callers—specifically `WorkflowRunner`:
1. **Caller-Managed Turn Indexing**: Callers must know how to calculate the next 0-based `turn_index`, requiring `WorkflowRunner._apersist_turn` to query turn counts from the store before instantiating a `ConversationTurn`.
2. **$O(N)$ Full-Session JSON Deserialization Penalty**: To evaluate `turn_index = len(existing_turns)` during normal turn persistence, `WorkflowRunner` executes `await self.memory.aget_turns(session_id)`. In SQLite, this deserializes all JSON columns (`attachments_json`, `citations_json`) for every turn across the entire conversation on every single user message.
3. **Write Concurrency and Race Hazards**: When multiple requests complete concurrently within the same session, both read the same turn count and assign duplicate `turn_index` values, causing silent data overwrite in SQLite.
4. **Non-Atomic Rewind Commit**: In-place rewind requires `WorkflowRunner` to issue two separate asynchronous operations: first calling `atruncate_from(session_id, rewind_turn_index)`, then `asave_turn(turn)`. If the process halts or the second write fails, the session is left truncated with the replacement turn lost.
5. **Rewind Hydration Penalty**: When hydrating memory for an in-place turn edit (`rewind_turn_index is not None`), `WorkflowRunner._ahydrate_memory` executes `aget_turns(session_id)` and manually slices `all_turns[max(0, rewind_turn_index - max_turns) : rewind_turn_index]`. This reintroduces full-session JSON deserialization into the pre-execution read path.

To eliminate this friction, the `ConversationMemoryAdapter` protocol seam must be deepened to encapsulate atomic turn appending, SQL-level monotonic index generation, atomic rewind truncation, and bounded pre-rewind message queries behind a high-leverage interface.

---

## Solution

Deepen the `ConversationMemoryAdapter` protocol seam and its concrete implementations (`SQLiteConversationStore`, `InMemoryConversationStore`, `NoOpMemoryStore`):

1. **Atomic Turn Append (`aappend_turn`)**:
   Introduce a high-leverage entrypoint on `ConversationMemoryAdapter` accepting scalar turn parameters and returning the persisted `ConversationTurn`. The adapter guarantees atomic monotonic indexing, rewind truncation, session timestamp updates, and persistence in a single transaction.
2. **Transactional SQL-Level Monotonic Indexing**:
   In `SQLiteConversationStore`, resolve `turn_index` using `SELECT COALESCE(MAX(turn_index) + 1, 0) FROM conversation_turns WHERE session_id = ?` under write lock, avoiding in-memory counting and deserialization.
3. **Atomic Rewind Commit**:
   Wrap turn deletion (`WHERE turn_index >= rewind_turn_index`) and replacement turn insertion in a single SQLite transaction block (`with conn:`), ensuring complete atomicity.
4. **Bounded Pre-Rewind Message Hydration (`before_turn_index`)**:
   Add `before_turn_index: int | None = None` to `aget_messages` and `aget_turns`. Push the `AND turn_index < before_turn_index` filter directly into SQL queries, eliminating Python-side turn slicing.
5. **Caller Streamlining in `WorkflowRunner`**:
   Refactor `_apersist_turn` to invoke `aappend_turn` in a single line. Refactor `_ahydrate_memory` to call `aget_messages` with `before_turn_index=rewind_turn_index`, collapsing branched logic into a single query.
6. **Preserved Seam Primitives**:
   Retain `asave_turn` for seed fixtures and test doubles; retain `atruncate_from` exclusively for the explicit `POST /api/chat/{session_id}/rewind` REST endpoint.

---

## User Stories

1. As an API caller sending sequential chat turns, I want the memory store to assign monotonic `turn_index` values in $O(1)$ database operations, so that conversation turn persistence does not degrade in latency as the session grows longer.
2. As a user performing an in-place turn rewind, I want the deletion of obsolete turns and insertion of the replacement turn to occur atomically in a single database transaction, so that my session is never left in a corrupt or partially deleted state if an error occurs.
3. As a user resuming a conversation from an edited turn, I want `WorkflowRunner` to hydrate only the bounded window of turns prior to that turn directly from the database, so that message hydration does not scan or deserialize unrelated future turns.
4. As an engineer maintaining `WorkflowRunner`, I want `_apersist_turn` to make a single call to `memory.aappend_turn(...)`, so that `WorkflowRunner` is completely decoupled from database indexing math and storage transaction ordering.
5. As an engineer maintaining `WorkflowRunner`, I want `_ahydrate_memory` to use a single unified call to `memory.aget_messages(session_id, limit=max_turns, before_turn_index=rewind_turn_index)`, so that standard execution and rewind hydration share the exact same code path.
6. As a concurrent multi-tab user interacting with the same session, I want atomic database-level index increments, so that simultaneous requests do not overwrite each other's turns due to client or runner index calculation races.
7. As a frontend client calling `POST /api/chat/{session_id}/rewind`, I want the explicit rewind endpoint to remain operational using `atruncate_from`, so that standalone turn deletion without immediate query generation continues to function.
8. As a developer running tests with `NoOpMemoryStore`, I want `aappend_turn` to return an ephemeral `ConversationTurn` stamped with `turn_index = rewind_turn_index or 0`, so that callers never encounter unexpected `None` return values.
9. As a developer writing isolated unit tests, I want `InMemoryConversationStore` to mimic the transactional index assignment and rewind truncation of SQLite in memory, so that test suites run deterministically without disk I/O.
10. As a QA developer writing test doubles, I want `asave_turn` to remain available on `ConversationMemoryAdapter`, so that existing unit tests constructing custom mock turn sequences continue to pass without modification.

---

## Implementation Decisions

### 1. `ConversationMemoryAdapter` Protocol Seam Changes
- Add `aappend_turn` to `ConversationMemoryAdapter` in `app/rag/memory/base.py`:
  ```python
  async def aappend_turn(
      self,
      session_id: str,
      user_query: str,
      assistant_answer: str,
      *,
      rewind_turn_index: int | None = None,
      attachments: list[dict[str, Any]] | None = None,
      citations: list[dict[str, Any]] | None = None,
      source_used: str = "direct",
      trace_id: str | None = None,
  ) -> ConversationTurn:
      """Atomically append a completed conversational turn, handling truncation on rewind."""
      ...
  ```
- Extend `aget_messages` and `aget_turns` on `ConversationMemoryAdapter`:
  ```python
  async def aget_messages(
      self,
      session_id: str,
      limit: int | None = None,
      before_turn_index: int | None = None,
  ) -> list[BaseMessage]: ...


  async def aget_turns(
      self,
      session_id: str,
      limit: int | None = None,
      before_turn_index: int | None = None,
  ) -> list[ConversationTurn]: ...
  ```
- Retain `asave_turn(turn: ConversationTurn) -> None` on the protocol seam.
- Retain `atruncate_from(session_id: str, turn_index: int) -> None` on the protocol seam.

### 2. `SQLiteConversationStore` Implementation Details
- In `aappend_turn`:
  - Acquire `self._write_lock`.
  - Dispatch a synchronous transaction function via `run_in_threadpool`.
  - Inside `with conn:`:
    - If `rewind_turn_index is not None`: execute `DELETE FROM conversation_turns WHERE session_id = ? AND turn_index >= ?`, and set `turn_index = rewind_turn_index`.
    - If `rewind_turn_index is None`: execute `SELECT COALESCE(MAX(turn_index) + 1, 0) AS next_index FROM conversation_turns WHERE session_id = ?`, and set `turn_index = int(cursor.fetchone()["next_index"])`.
    - Upsert parent session into `conversation_sessions` with `updated_at = now_iso`.
    - Insert turn into `conversation_turns`.
    - Return instantiated `ConversationTurn` model.
- In `aget_turns`:
  - Incorporate `before_turn_index` parameter into SQL query generation:
    - If `before_turn_index is not None`, add `AND turn_index < ?` to the `WHERE` clause.
    - If `limit is not None`, wrap with inner descending query and outer ascending sort:
      ```sql
      SELECT {columns} FROM (
          SELECT {columns} FROM conversation_turns
          WHERE session_id = ? AND turn_index < ?
          ORDER BY turn_index DESC LIMIT ?
      ) ORDER BY turn_index ASC;
      ```

### 3. `InMemoryConversationStore` Implementation Details
- In `aappend_turn`:
  - Acquire `self._lock`.
  - If `rewind_turn_index is not None`: prune `[t for t in turns if t.turn_index < rewind_turn_index]`, set `turn_index = rewind_turn_index`.
  - If `rewind_turn_index is None`: set `turn_index = max([t.turn_index for t in turns], default=-1) + 1`.
  - Construct `ConversationTurn`, append to `self._turns[session_id]`, sort by `turn_index ASC`.
  - Upsert `SessionInfo` with updated `turn_count` and `updated_at`.
  - Return a deep copy of the turn.
- In `aget_turns`:
  - Filter turns by `turn.turn_index < before_turn_index` when `before_turn_index is not None`.
  - Apply `limit` to the tail of the filtered list.

### 4. `NoOpMemoryStore` Implementation Details
- In `aappend_turn`:
  - Return an ephemeral `ConversationTurn(session_id=session_id, turn_index=rewind_turn_index or 0, user_query=user_query, assistant_answer=assistant_answer, ...)` without persisting.
- In `aget_messages` and `aget_turns`:
  - Accept `before_turn_index: int | None = None` and return empty lists.

### 5. `WorkflowRunner` Orchestration Refactoring
- In `_apersist_turn`:
  - Remove all manual calls to `self.memory.atruncate_from` and `self.memory.aget_turns`.
  - Invoke:
    ```python
    turn = await self.memory.aappend_turn(
        session_id=session_id,
        user_query=request.question,
        assistant_answer=domain_output.answer,
        rewind_turn_index=rewind_turn_index,
        attachments=getattr(request, "attachments", None) or [],
        citations=domain_output.citations,
        source_used=domain_output.source_used,
        trace_id=tid,
    )
    ```
- In `_ahydrate_memory`:
  - Remove the branched rewind slicing logic (`all_turns = await self.memory.aget_turns(session_id); sliced = ...`).
  - Collapse into:
    ```python
    hydrated = await self.memory.aget_messages(
        session_id=session_id,
        limit=max_turns,
        before_turn_index=rewind_turn_index,
    )
    ```
- Eliminate direct references to `atruncate_from` in `WorkflowRunner`.

---

## Testing Decisions

### Guiding Principles
- Tests must verify external behavioral contracts across the `ConversationMemoryAdapter` protocol seam rather than private SQLite helpers or internal locking state.
- Existing tests for `WorkflowRunner` memory orchestration must pass with updated mocks that reflect `aappend_turn`.

### Target Modules & Test Suites
1. **`tests/rag/test_conversation_memory_sqlite.py`**:
   - Test `aappend_turn` normal monotonic sequencing (`turn_index = 0, 1, 2`).
   - Test `aappend_turn` with `rewind_turn_index = 1` pruning turns $\ge 1$ and appending the replacement turn at `turn_index = 1`.
   - Test `aget_messages` and `aget_turns` with `before_turn_index` and `limit`.
   - Test concurrent `aappend_turn` invocations ensuring no collision or duplicate `turn_index`.
2. **`tests/rag/test_conversation_memory_in_memory.py`**:
   - Mirror SQLite tests against `InMemoryConversationStore`.
3. **`tests/rag/test_conversation_memory_base.py`**:
   - Verify `ConversationMemoryAdapter` protocol conformance for all three adapters.
   - Verify `NoOpMemoryStore.aappend_turn` returns a valid non-null `ConversationTurn`.
4. **`tests/rag/test_workflow_runner_memory.py`**:
   - Update mock memory doubles to implement `aappend_turn`.
   - Verify `WorkflowRunner._apersist_turn` delegates to `aappend_turn` with correct parameters.
   - Verify `WorkflowRunner._ahydrate_memory` passes `before_turn_index` during rewind.
5. **Regression Verification**:
   - Run the complete test suite (`uv run pytest tests`) to ensure all 651 existing tests continue to pass.

---

## Out of Scope

- Modifying the SQLite schema (`conversation_sessions` and `conversation_turns` table schemas remain unchanged).
- Modifying REST endpoint URLs or HTTP request/response payloads (`/api/chat/{session_id}/rewind` and `/api/chat/{session_id}/fork` contracts remain unchanged).
- Refactoring `WorkflowRunner` streaming generator or execution profile resolution (designated for Candidate 2).
- Changing prompt condensation logic or graph topologies.

---

## Further Notes

- This specification implements **Candidate 1** from the architectural review and directly fulfills **ADR 0017**.
- By shifting storage indexing into SQLite and eliminating $O(N)$ full-session JSON deserialization, persistent multi-turn conversational chat maintains consistent sub-millisecond overhead regardless of conversation length.
