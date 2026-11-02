# ADR 0017: Deepen Conversation Memory Seam with Atomic Append and Bounded Message Hydration

## Status
Accepted (Extends ADR 0016 and ADR 0010)

## Context
In ADR-0016, the `ConversationMemoryAdapter` protocol seam was introduced to provide multi-turn conversation persistence, session resumption, turn truncation, and session branching across all workflow types.

However, the initial protocol exposed relatively low-level primitives (`asave_turn`, `atruncate_from`, `aget_turns`), which caused significant architectural and performance friction at the caller seam (`WorkflowRunner`):
1. **Storage Index Leakage**: Callers had to compute storage mechanics (`turn_index = len(existing_turns)`) before instantiating and saving a `ConversationTurn`.
2. **O(N) Full-Session Deserialization Penalty**: To determine `turn_index` during normal turn persistence, `WorkflowRunner._apersist_turn` called `await self.memory.aget_turns(session_id)`, causing SQLite to deserialize all JSON columns (`attachments_json`, `citations_json`) for every turn across the entire session on every single message.
3. **Concurrency Hazard**: If concurrent queries completed for a session, both read the same `len(existing_turns)` and collided on `turn_index`, unintentionally overwriting turns.
4. **Non-Atomic Rewind Commit**: In-place rewind required two separate async calls from the runner: first `atruncate_from`, then `asave_turn`. If the turn insert failed or the process halted midway, the session remained truncated without the replacement turn.
5. **Rewind Hydration Penalty**: `WorkflowRunner._ahydrate_memory` also called `aget_turns(session_id)` to manually slice historical turns prior to `rewind_turn_index`, re-triggering full session deserialization on every turn edit.

To achieve clean module depth, caller leverage, and storage locality, the `ConversationMemoryAdapter` protocol seam must encapsulate atomic turn insertion, storage index calculation, rewind truncation, and bounded pre-rewind message queries.

---

## Decisions

### 1. Atomic `aappend_turn` Protocol Method
Extend `ConversationMemoryAdapter` with a deep, atomic turn appending method:
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
```
- The method takes scalar turn parameters, eliminating caller instantiation of `ConversationTurn`.
- Returns the canonical `ConversationTurn` stamped with the authoritative `turn_index` and `created_at`.
- Guaranteed non-null return type across all adapter implementations (including `NoOpMemoryStore`).

### 2. Transactional Atomicity and SQL-Level $O(1)$ Turn Indexing in `SQLiteConversationStore`
- In `SQLiteConversationStore`, `aappend_turn` executes within a single SQLite transaction (`with conn:`) protected by `self._write_lock`:
  - **Rewind execution**: If `rewind_turn_index` is provided, delete all turns where `session_id = ? AND turn_index >= ?`, then assign `turn_index = rewind_turn_index`.
  - **Normal execution**: If `rewind_turn_index is None`, assign `turn_index = (SELECT COALESCE(MAX(turn_index) + 1, 0) FROM conversation_turns WHERE session_id = ?)`.
  - Upsert parent session record in `conversation_sessions`, setting `updated_at` to the current timestamp.
  - Insert new row into `conversation_turns`.
- Eliminates in-memory turn counting and JSON deserialization overhead during writes ($O(1)$ performance).
- Guarantees that rewind truncation and replacement turn persistence succeed or fail together atomically.

### 3. In-Memory Atomic Mutation in `InMemoryConversationStore`
- `InMemoryConversationStore.aappend_turn` acquires `self._lock`:
  - If `rewind_turn_index is not None`: drops turns with `turn_index >= rewind_turn_index` and sets `turn_index = rewind_turn_index`.
  - If `rewind_turn_index is None`: sets `turn_index = max([t.turn_index for t in turns], default=-1) + 1`.
  - Appends and sorts turns, updates session `turn_count` and `updated_at`, and returns a deep copy of the turn.

### 4. Ephemeral Return on `NoOpMemoryStore`
- `NoOpMemoryStore.aappend_turn` constructs and returns an ephemeral `ConversationTurn` with `turn_index = rewind_turn_index or 0`, ensuring callers never require defensive `None` checks.

### 5. Bounded Message Retrieval via `before_turn_index`
- Add `before_turn_index: int | None = None` parameter to both `aget_messages` and `aget_turns` on `ConversationMemoryAdapter`.
- `SQLiteConversationStore` incorporates `AND turn_index < ?` directly into SQL queries before applying `ORDER BY turn_index DESC LIMIT ?`.
- `WorkflowRunner._ahydrate_memory` collapses into a single call:
  ```python
  hydrated = await self.memory.aget_messages(
      session_id,
      limit=max_turns,
      before_turn_index=rewind_turn_index,
  )
  ```
  completely removing Python-side turn slicing and full-history loads during rewinds.

### 6. Caller Streamlining in `WorkflowRunner`
- Replace `WorkflowRunner._apersist_turn` internals with a single `await self.memory.aappend_turn(...)` invocation.
- `WorkflowRunner` no longer manages storage indexing, pre-truncation ordering, or turn count calculations.
- Direct calls to `atruncate_from` are eliminated from `WorkflowRunner`.

### 7. Seam Backward Compatibility
- Retain `asave_turn` on `ConversationMemoryAdapter` for test fixtures and seed scripts.
- Retain `atruncate_from` on `ConversationMemoryAdapter` strictly for explicit REST endpoints (`POST /api/chat/{session_id}/rewind`) and administrative tasks where a user truncates a session without generating a new turn.

---

## Consequences

### Positive
- **Deep Seam**: The `ConversationMemoryAdapter` interface expands capability while simplifying caller integration.
- **Maximum Caller Leverage**: `WorkflowRunner` persists turns in 1 call instead of 4 sequential steps.
- **Strict Storage Locality**: Turn numbering, transaction integrity, and truncation invariants are consolidated inside the adapters.
- **Elimination of $O(N)$ Deserialization**: Both turn appending and rewind hydration execute in $O(1)$ and $O(\text{limit})$ SQL operations, avoiding full-session deserialization.
- **Concurrency Resilience**: Turn indices are generated atomically in SQLite under write transactions, eliminating race-condition turn overwrites.

### Negative / Trade-Offs
- `ConversationMemoryAdapter` gains one additional protocol method (`aappend_turn`) and one query parameter (`before_turn_index`), requiring implementation across all three adapter classes.
