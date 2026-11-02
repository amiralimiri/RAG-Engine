# Ticket 01: Protocol Seam Deepening & No-Op Adapter

- **Milestone:** Deepen Conversation Memory Seam (ADR-0017)
- **Status:** ready-for-agent
- **Blocked by:** None (can start immediately)
- **Estimated Scope:** Small

---

## 1. Goal & Context
Deepen the `ConversationMemoryAdapter` protocol seam in `app/rag/memory/base.py` by introducing `aappend_turn` and extending `aget_messages` and `aget_turns` with `before_turn_index`. Implement the updated signatures in `NoOpMemoryStore` (`app/rag/memory/noop.py`) to guarantee non-null return values.

---

## 2. Technical Requirements

### 2.1 Protocol Seam (`app/rag/memory/base.py`)
- Add `aappend_turn` to `ConversationMemoryAdapter`:
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
- Add `before_turn_index: int | None = None` to `aget_messages` and `aget_turns`:
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
- Retain existing `asave_turn`, `atruncate_from`, `aclear`, `afork_session`, `alist_sessions`, and `aget_session`.

### 2.2 No-Op Store (`app/rag/memory/noop.py`)
- Implement `aappend_turn`:
  - Return an ephemeral `ConversationTurn(session_id=session_id, turn_index=rewind_turn_index or 0, user_query=user_query, assistant_answer=assistant_answer, attachments=attachments or [], citations=citations or [], source_used=source_used, trace_id=trace_id)`.
  - Must return `ConversationTurn`, never `None`.
- Update `aget_messages` and `aget_turns` to accept `before_turn_index: int | None = None` and return `[]`.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- In `tests/rag/test_conversation_memory_base.py`:
  - Add test verifying `isinstance(adapter, ConversationMemoryAdapter)` with classes defining `aappend_turn`.
  - Add test verifying `NoOpMemoryStore.aappend_turn` returns a valid `ConversationTurn` with matching `turn_index` (default `0` or `rewind_turn_index`).
  - Add test verifying `NoOpMemoryStore.aget_messages` and `aget_turns` accept `before_turn_index` without error.

### Green (Implementation)
- Update `app/rag/memory/base.py` and `app/rag/memory/noop.py`.

---

## Acceptance Criteria
- [ ] `ConversationMemoryAdapter` defines `aappend_turn` with complete type annotations.
- [ ] `ConversationMemoryAdapter.aget_messages` and `aget_turns` accept optional `before_turn_index`.
- [ ] `NoOpMemoryStore.aappend_turn` returns a fully instantiated `ConversationTurn` model.
- [ ] `tests/rag/test_conversation_memory_base.py` passes cleanly.
