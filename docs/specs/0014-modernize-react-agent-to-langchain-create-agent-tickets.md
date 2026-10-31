# Work Packages / Implementation Tickets: Modernize ReAct Agent to LangChain `create_agent`

- **Specification Reference:** [`docs/specs/0014-modernize-react-agent-to-langchain-create-agent-spec.md`](./0014-modernize-react-agent-to-langchain-create-agent-spec.md)
- **ADR Reference:** [`docs/adr/0014-modernize-react-agent-to-langchain-create-agent.md`](../adr/0014-modernize-react-agent-to-langchain-create-agent.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/workflow-execution.md`](../agents/workflow-execution.md)
- **Status:** Proposed

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│ Ticket 01: Node Constants, Source Resolution           │
│ Harmonization & Unit Tests                             │
│ (.scratch/01-node-constants-and-source-harmony.md)     │
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 02: Canonical `create_agent` Compiler Migration │
│ (.scratch/02-create-agent-compiler-migration.md)       │
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 03: End-to-End Streaming, Telemetry & Full      │
│ Conformance Gate                                       │
│ (.scratch/03-streaming-telemetry-and-conformance.md)   │
└────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Node Constants, Source Resolution Harmonization & Unit Tests | `app/rag/workflows/react_agent.py`, `tests/rag/test_react_agent.py` | None | `completed` |
| **Ticket 02** | Canonical `create_agent` Compiler Migration | `app/rag/workflows/react_agent.py`, `tests/rag/test_react_agent.py` | Ticket 01 | `proposed` |
| **Ticket 03** | End-to-End Streaming, Telemetry & Full Conformance Gate | `tests/test_chat_stream.py`, `tests/test_e2e_observability.py`, Full pytest suite | Ticket 02 | `proposed` |

---

## Ticket Locations

1. [Ticket 01: Node Constants, Source Resolution Harmonization & Unit Tests](../../.scratch/01-node-constants-and-source-harmony.md)
2. [Ticket 02: Canonical `create_agent` Compiler Migration](../../.scratch/02-create-agent-compiler-migration.md)
3. [Ticket 03: End-to-End Streaming, Telemetry & Full Conformance Gate](../../.scratch/03-streaming-telemetry-and-conformance.md)
