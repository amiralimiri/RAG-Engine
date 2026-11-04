# Work Packages / Implementation Tickets: Deepen Conversation Memory with Session Aggregate and Atomic Branching Seam

- **Specification Reference:** [`docs/specs/0023-deepen-conversation-memory-session-aggregate-spec.md`](./0023-deepen-conversation-memory-session-aggregate-spec.md)
- **ADR Reference:** [`docs/adr/0023-deepen-conversation-memory-session-aggregate.md`](../adr/0023-deepen-conversation-memory-session-aggregate.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/conversation-memory.md`](../agents/conversation-memory.md)
- **Status:** Proposed / Ready for Review
- **Triage Label:** `ready-for-agent`

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 01: ConversationSession Domain Aggregate & Protocol Seam        │
│ (issues/01-conversation-session-domain-aggregate-and-protocol.md)      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 02: InMemoryConversationStore & NoOpMemoryStore Implementation  │
│ (issues/02-in-memory-and-noop-memory-adapters.md)                      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 03: SQLiteConversationStore Single-Transaction Implementation   │
│ (issues/03-sqlite-single-transaction-adapter.md)                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 04: Presentation Layer Integration & Regression Verification   │
│ (issues/04-presentation-layer-routes-and-regression.md)                │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | ConversationSession Domain Aggregate & Protocol Seam | `app/rag/memory/base.py`, `tests/rag/test_conversation_memory_base.py` | None | `ready-for-agent` |
| **Ticket 02** | InMemoryConversationStore & NoOpMemoryStore Implementation | `app/rag/memory/in_memory.py`, `app/rag/memory/noop.py`, `tests/rag/test_conversation_memory_in_memory.py` | Ticket 01 | `ready-for-agent` |
| **Ticket 03** | SQLiteConversationStore Single-Transaction Implementation | `app/rag/memory/sqlite.py`, `tests/rag/test_conversation_memory_sqlite.py` | Ticket 02 | `ready-for-agent` |
| **Ticket 04** | Presentation Layer Integration & Regression Verification | `app/api/schemas.py`, `app/api/routes.py`, `tests/api/test_routes_chat_memory.py`, regression suite | Ticket 03 | `ready-for-agent` |

---

## Ticket Locations

1. [Ticket 01: ConversationSession Domain Aggregate & Protocol Seam](./0023-deepen-conversation-memory-session-aggregate/issues/01-conversation-session-domain-aggregate-and-protocol.md)
2. [Ticket 02: InMemoryConversationStore & NoOpMemoryStore Implementation](./0023-deepen-conversation-memory-session-aggregate/issues/02-in-memory-and-noop-memory-adapters.md)
3. [Ticket 03: SQLiteConversationStore Single-Transaction Implementation](./0023-deepen-conversation-memory-session-aggregate/issues/03-sqlite-single-transaction-adapter.md)
4. [Ticket 04: Presentation Layer Integration & Regression Verification](./0023-deepen-conversation-memory-session-aggregate/issues/04-presentation-layer-routes-and-regression.md)
