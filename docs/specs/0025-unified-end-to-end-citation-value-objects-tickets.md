# Work Packages / Implementation Tickets: Unified End-to-End Citation Value Objects Across Memory and Transport Seams

- **Specification Reference:** [`docs/specs/0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams-spec.md`](./0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams-spec.md)
- **ADR Reference:** [`docs/adr/0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams.md`](../adr/0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md)
- **Status:** 100% Completed
- **Triage Label:** `completed`

---

## Dependency Graph

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Ticket 01: Relocate Citation Domain Model, Boundary Coercion             │
│            & Zero-Shim Invariants                                        │
│ (issues/01-relocate-citation-domain-model-boundary-coercion-and-zero-    │
│  shim-invariants.md)                                                     │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
┌───────────────────────────────┐         ┌───────────────────────────────┐
│ Ticket 02: End-to-End         │         │ Ticket 03: Strongly-Typed     │
│ Workflow Engine & Agent       │         │ Conversation Memory Storage   │
│ Graph State Typing            │         │ & SQLite Deserialization      │
│ (issues/02-end-to-end-        │         │ (issues/03-strongly-typed-    │
│  workflow-engine-and-agent-   │         │  conversation-memory-storage- │
│  graph-state-typing.md)       │         │  and-sqlite-deserialization.md)│
└───────────────┬───────────────┘         └───────────────┬───────────────┘
                │                                         │
                └──────────────────┬──────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Ticket 04: Transport Schemas, Streaming SSE Emission                     │
│            & Evaluation Seam Alignment                                   │
│ (issues/04-transport-schemas-streaming-sse-emission-and-evaluation-      │
│  seam-alignment.md)                                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Blockers | Status |
|---|---|---|---|
| **Ticket 01** | Relocate Citation Domain Model, Boundary Coercion & Zero-Shim Invariants | None | `completed` |
| **Ticket 02** | End-to-End Workflow Engine & Agent Graph State Typing | Ticket 01 | `completed` |
| **Ticket 03** | Strongly-Typed Conversation Memory Storage & SQLite Deserialization | Ticket 01 | `completed` |
| **Ticket 04** | Transport Schemas, Streaming SSE Emission & Evaluation Seam Alignment | Ticket 02, Ticket 03 | `completed` |

---

## Ticket Locations

1. [Ticket 01: Relocate Citation Domain Model, Boundary Coercion & Zero-Shim Invariants](./0025-unified-end-to-end-citation-value-objects/issues/01-relocate-citation-domain-model-boundary-coercion-and-zero-shim-invariants.md)
2. [Ticket 02: End-to-End Workflow Engine & Agent Graph State Typing](./0025-unified-end-to-end-citation-value-objects/issues/02-end-to-end-workflow-engine-and-agent-graph-state-typing.md)
3. [Ticket 03: Strongly-Typed Conversation Memory Storage & SQLite Deserialization](./0025-unified-end-to-end-citation-value-objects/issues/03-strongly-typed-conversation-memory-storage-and-sqlite-deserialization.md)
4. [Ticket 04: Transport Schemas, Streaming SSE Emission & Evaluation Seam Alignment](./0025-unified-end-to-end-citation-value-objects/issues/04-transport-schemas-streaming-sse-emission-and-evaluation-seam-alignment.md)
