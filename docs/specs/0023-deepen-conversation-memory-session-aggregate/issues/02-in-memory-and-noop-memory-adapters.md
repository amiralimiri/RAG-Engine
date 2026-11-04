# Ticket 02: InMemoryConversationStore & NoOpMemoryStore Implementation

- **ADR Reference:** [`docs/adr/0023-deepen-conversation-memory-session-aggregate.md`](../../../adr/0023-deepen-conversation-memory-session-aggregate.md)
- **Specification Reference:** [`docs/specs/0023-deepen-conversation-memory-session-aggregate-spec.md`](../0023-deepen-conversation-memory-session-aggregate-spec.md)
- **Target Files:**
  - `app/rag/memory/in_memory.py`
  - `app/rag/memory/noop.py`
  - `tests/rag/test_conversation_memory_in_memory.py`
- **Blocked by:** Ticket 01
- **Status:** ready-for-agent

---

## What to build

Implement the deepened `aget_session` and `afork_session` methods in `InMemoryConversationStore` and `NoOpMemoryStore`. `InMemoryConversationStore.aget_session` returns a deep copy of `ConversationSession` with its turn history (sliced chronologically to the latest $N$ turns if `limit` is set). `InMemoryConversationStore.afork_session` raises `KeyError` if `source_session_id` does not exist and `ValueError` if `upto_turn_index < -1`, returning the cloned `ConversationSession` aggregate. `NoOpMemoryStore` returns `None` for `aget_session` and an ephemeral `ConversationSession` for `afork_session`.

## Acceptance criteria

- [ ] `InMemoryConversationStore.aget_session(session_id, limit=None)` returns `ConversationSession | None` containing the chronological `turns` sequence.
- [ ] When `limit` is provided to `InMemoryConversationStore.aget_session`, only the latest $N$ turns are returned, ordered ascending by `turn_index`.
- [ ] `InMemoryConversationStore.afork_session` raises `KeyError` if `source_session_id` is not present in `self._sessions`.
- [ ] `InMemoryConversationStore.afork_session` raises `ValueError` if `upto_turn_index < -1`.
- [ ] `InMemoryConversationStore.afork_session` returns `ConversationSession` containing the cloned turn subset and accurate metadata.
- [ ] `NoOpMemoryStore.aget_session(session_id)` returns `None`.
- [ ] `NoOpMemoryStore.afork_session(...)` returns an ephemeral `ConversationSession` with `session_id=new_session_id or uuid` and `turns=[]`.
- [ ] `tests/rag/test_conversation_memory_in_memory.py` covers full aggregate return, limit windowing, and invariant exceptions.
