# Ticket 01: ConversationSession Domain Aggregate & Protocol Seam

- **ADR Reference:** [`docs/adr/0023-deepen-conversation-memory-session-aggregate.md`](../../../adr/0023-deepen-conversation-memory-session-aggregate.md)
- **Specification Reference:** [`docs/specs/0023-deepen-conversation-memory-session-aggregate-spec.md`](../0023-deepen-conversation-memory-session-aggregate-spec.md)
- **Target Files:**
  - `app/rag/memory/base.py`
  - `tests/rag/test_conversation_memory_base.py`
- **Blocked by:** None (can start immediately)
- **Status:** closed

---

## What to build

Define the `ConversationSession` domain aggregate model in `app/rag/memory/base.py` subtyping `SessionInfo`, containing `turns: list[ConversationTurn] = Field(default_factory=list)`. Update the `ConversationMemoryAdapter` protocol seam signatures for `aget_session` to return `ConversationSession | None` and `afork_session` to return `ConversationSession`.

## Acceptance criteria

- [x] `ConversationSession` is defined in `app/rag/memory/base.py` as a subclass of `SessionInfo` with a typed `turns: list[ConversationTurn]` field.
- [x] Any instance of `ConversationSession` is an instance of `SessionInfo` (`issubclass(ConversationSession, SessionInfo)` and `isinstance(s, SessionInfo)` are True).
- [x] `ConversationMemoryAdapter.aget_session(session_id: str, limit: int | None = None) -> ConversationSession | None` is defined on the protocol.
- [x] `ConversationMemoryAdapter.afork_session(source_session_id: str, upto_turn_index: int, new_session_id: str | None = None) -> ConversationSession` is defined on the protocol.
- [x] `app/rag/memory/base.py` exports `ConversationSession` in `__all__` alongside `ConversationTurn` and `SessionInfo`.
- [x] `tests/rag/test_conversation_memory_base.py` validates protocol runtime checkability, inheritance, and default turns factory.
