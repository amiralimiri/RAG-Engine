# Ticket 02: In-Memory Adapter Atomic Append & Bounded Slicing

- **Milestone:** Deepen Conversation Memory Seam (ADR-0017)
- **Status:** ready-for-agent
- **Blocked by:** Ticket 01 (Protocol Seam Deepening & No-Op Adapter)
- **Estimated Scope:** Small

---

## 1. Goal & Context
Implement `aappend_turn` and bounded retrieval via `before_turn_index` in `InMemoryConversationStore` (`app/rag/memory/in_memory.py`), ensuring that in-memory test doubles accurately mirror the transactional behavior, indexing rules, and rewind mechanics of the production store under thread-safe locking.

---

## 2. Technical Requirements

### 2.1 `InMemoryConversationStore` Implementation (`app/rag/memory/in_memory.py`)
- Implement `aappend_turn`:
  - Acquire `self._lock`.
  - Handle rewind truncation: if `rewind_turn_index is not None`, prune turns where `turn_index >= rewind_turn_index`, and set `turn_index = rewind_turn_index`.
  - Handle normal append: if `rewind_turn_index is None`, calculate `turn_index = max([t.turn_index for t in turns], default=-1) + 1`.
  - Construct `ConversationTurn`, append to `self._turns[session_id]`, and sort by `turn_index ASC`.
  - Upsert `SessionInfo`: update `turn_count = len(turns)` and `updated_at = datetime.now(UTC)`.
  - Return `turn.model_copy(deep=True)`.
- Update `aget_turns`:
  - Accept `before_turn_index: int | None = None`.
  - If `before_turn_index is not None`: filter `turns = [t for t in turns if t.turn_index < before_turn_index]`.
  - Apply `limit` to the tail of the filtered list (`turns[-limit:] if limit > 0 else []`).
- Update `aget_messages`:
  - Accept `before_turn_index: int | None = None` and pass through to `aget_turns(session_id, limit=limit, before_turn_index=before_turn_index)`.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- In `tests/rag/test_conversation_memory_in_memory.py`:
  - Test sequential `aappend_turn` creates monotonic indices (`0, 1, 2`) and updates `SessionInfo.turn_count`.
  - Test `aappend_turn` with `rewind_turn_index = 1` deletes turns $\ge 1$, persists new turn at index `1`, and updates session metadata.
  - Test `aget_turns` and `aget_messages` with `before_turn_index=2` and `limit=1` returns only turn index `1`.
  - Test multi-turn append with attachments and citations preserves all attributes.

### Green (Implementation)
- Implement changes in `app/rag/memory/in_memory.py`.

---

## Acceptance Criteria
- [ ] `InMemoryConversationStore.aappend_turn` assigns `0, 1, 2...` monotonically on sequential calls.
- [ ] Rewind truncation in `aappend_turn` removes prior turns $\ge k$ and inserts the replacement turn at `turn_index = k`.
- [ ] `aget_turns` and `aget_messages` properly bound results when `before_turn_index` is specified.
- [ ] All unit tests in `tests/rag/test_conversation_memory_in_memory.py` pass.
