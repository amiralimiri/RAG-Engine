# Work Packages / Implementation Tickets: Decouple Workflow Execution from Transport Wire Protocol and Lifecycle Side-Effects

- **Specification Reference:** [`docs/specs/0019-decouple-workflow-execution-from-transport-wire-protocol-spec.md`](./0019-decouple-workflow-execution-from-transport-wire-protocol-spec.md)
- **ADR Reference:** [`docs/adr/0019-decouple-workflow-execution-from-transport-wire-protocol.md`](../adr/0019-decouple-workflow-execution-from-transport-wire-protocol.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/workflow-execution.md`](../agents/workflow-execution.md)
- **Status:** Ready for Implementation

---

## Dependency Graph

```
           ┌───────────────────────────────────────────────┐
           │ Ticket 01: Domain Event Subsystem & Schemas   │
           │ (.scratch/0019-.../issues/01-domain-event...) │
           └───────────────────────┬───────────────────────┘
                                   │
                 ┌─────────────────┴─────────────────┐
                 ▼                                   ▼
┌─────────────────────────────────┐ ┌─────────────────────────────────┐
│ Ticket 02: Execution Lifecycle  │ │ Ticket 03: Transport Wire Seam  │
│ Listener Protocol & Adapters    │ │ & SseStreamAdapter              │
│ (.scratch/0019-.../issues/02...)│ │ (.scratch/0019-.../issues/03...)│
└────────────────┬────────────────┘ └────────────────┬────────────────┘
                 │                                   │
                 ▼                                   │
┌─────────────────────────────────┐                  │
│ Ticket 04: Refactor Runner to   │                  │
│ Event Generator & Memory Seam   │                  │
│ (.scratch/0019-.../issues/04...)│                  │
└────────────────┬────────────────┘                  │
                 │                                   │
                 └─────────────────┬─────────────────┘
                                   ▼
           ┌───────────────────────────────────────────────┐
           │ Ticket 05: HTTP Route Integration, E2E Wire   │
           │ Streaming & Regression Verification           │
           │ (.scratch/0019-.../issues/05-http-route...)   │
           └───────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Domain Event Subsystem & Schemas | `app/rag/workflows/events.py`, `app/rag/workflows/__init__.py`, `tests/rag/test_workflow_events.py` | None | `ready-for-agent` |
| **Ticket 02** | Execution Lifecycle Listener Protocol & Concrete Adapters | `app/rag/workflows/listeners.py`, `app/rag/workflows/__init__.py`, `tests/rag/test_workflow_listeners.py` | Ticket 01 | `ready-for-agent` |
| **Ticket 03** | Transport Wire Seam & SseStreamAdapter | `app/api/streaming.py`, `app/api/__init__.py`, `tests/api/test_sse_stream_adapter.py` | Ticket 01 | `ready-for-agent` |
| **Ticket 04** | Refactor WorkflowRunner to Pure Event Generator & Explicit Memory | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_execution.py`, `tests/rag/test_workflow_runner.py` | Ticket 02 | `ready-for-agent` |
| **Ticket 05** | HTTP Route Integration, E2E Wire Streaming & Regression Verification | `app/api/routes.py`, `tests/test_chat_stream.py`, full `pytest` regression run | Ticket 03, Ticket 04 | `ready-for-agent` |

---

## Ticket Locations

1. [Ticket 01: Domain Event Subsystem & Schemas](../../.scratch/0019-decouple-workflow-execution-from-transport-wire-protocol/issues/01-domain-event-subsystem-and-schemas.md)
2. [Ticket 02: Execution Lifecycle Listener Protocol & Concrete Adapters](../../.scratch/0019-decouple-workflow-execution-from-transport-wire-protocol/issues/02-execution-lifecycle-listener-protocol-and-adapters.md)
3. [Ticket 03: Transport Wire Seam & SseStreamAdapter](../../.scratch/0019-decouple-workflow-execution-from-transport-wire-protocol/issues/03-transport-wire-seam-and-sse-stream-adapter.md)
4. [Ticket 04: Refactor WorkflowRunner to Pure Event Generator & Explicit Memory](../../.scratch/0019-decouple-workflow-execution-from-transport-wire-protocol/issues/04-refactor-runner-to-event-generator-and-memory-seam.md)
5. [Ticket 05: HTTP Route Integration, E2E Wire Streaming & Regression Verification](../../.scratch/0019-decouple-workflow-execution-from-transport-wire-protocol/issues/05-http-route-integration-and-regression-verification.md)
