# Work Packages / Implementation Tickets: Unify Workflow Execution Lifecycle and Side-Effect Dispatch

- **Specification Reference:** [`docs/specs/0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch-spec.md`](./0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch-spec.md)
- **ADR Reference:** [`docs/adr/0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch.md`](../adr/0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/workflow-execution.md`](../agents/workflow-execution.md)
- **Status:** Completed (4 Tickets)

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 01: Unified Listener Dispatch Engine & Fail-Open Semantics      │
│ (issues/01-unified-listener-dispatch-engine.md)                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 02: Route Synchronous & Async Execution Through Listener Seam   │
│ (issues/02-route-sync-and-async-execution-through-listener-seam.md)    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 03: Prune Private Bypass Methods & Mutable Property Setters     │
│ (issues/03-prune-private-bypass-methods-and-mutable-setters.md)       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 04: Transport-Agnostic Interface & HTTP Route Modernization     │
│ (issues/04-transport-agnostic-interface-and-http-route-modernization.md)│
└────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Unified Listener Dispatch Engine & Fail-Open Semantics | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_execution.py` | None | `completed` |
| **Ticket 02** | Route Synchronous & Async Execution Through Listener Seam | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_execution.py`, `tests/rag/test_workflow_runner.py` | Ticket 01 | `completed` |
| **Ticket 03** | Prune Private Bypass Methods & Mutable Property Setters | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_runner.py`, `tests/test_online_evaluation.py` | Ticket 02 | `completed` |
| **Ticket 04** | Transport-Agnostic Interface & HTTP Route Modernization | `app/api/routes.py`, `app/rag/workflows/runner.py`, `tests/api/test_routes_chat_memory.py`, `tests/test_chat_stream.py` | Ticket 03 | `completed` |

---

## Ticket Locations

1. [Ticket 01: Unified Listener Dispatch Engine & Fail-Open Semantics](./0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch/issues/01-unified-listener-dispatch-engine.md)
2. [Ticket 02: Route Synchronous & Async Execution Through Listener Seam](./0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch/issues/02-route-sync-and-async-execution-through-listener-seam.md)
3. [Ticket 03: Prune Private Bypass Methods & Mutable Property Setters](./0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch/issues/03-prune-private-bypass-methods-and-mutable-setters.md)
4. [Ticket 04: Transport-Agnostic Interface & HTTP Route Modernization](./0020-unify-workflow-execution-lifecycle-and-side-effect-dispatch/issues/04-transport-agnostic-interface-and-http-route-modernization.md)
