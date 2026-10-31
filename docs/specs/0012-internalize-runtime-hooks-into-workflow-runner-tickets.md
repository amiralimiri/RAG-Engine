# Work Packages / Implementation Tickets: Internalize Runtime Hooks into WorkflowRunner

- **Specification Reference:** [`docs/specs/0012-internalize-runtime-hooks-into-workflow-runner-spec.md`](./0012-internalize-runtime-hooks-into-workflow-runner-spec.md)
- **ADR Reference:** [`docs/adr/0012-internalize-runtime-hooks-into-workflow-runner.md`](../adr/0012-internalize-runtime-hooks-into-workflow-runner.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/workflow-execution.md`](../agents/workflow-execution.md)
- **Status:** Completed

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│ Ticket 01: WorkflowRunner Constructor Auto-Wiring,    │
│ Interface Shrinkage & Singleton Reset                  │
│ (.scratch/tickets/01-runner-constructor-and-reset.md)  │
└───────────┬────────────────────────────┬───────────────┘
            │                            │
            ▼                            ▼
┌──────────────────────────────┐ ┌────────────────────────────────────────────────┐
│ Ticket 02: Transport Layer   │ │ Ticket 03: Telemetry, Contexts, and            │
│ Simplification (routes.py)   │ │ Experiment Caller Migration                    │
│ (.scratch/tickets/02-routes..│ │ (.scratch/tickets/03-telemetry-and-callers... │
└───────────┬──────────────────┘ └───────────────────────┬────────────────────────┘
            │                                            │
            └────────────────────┬───────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 04: WorkflowRunner Test Suite Modernization     │
│ & Conformance Gate                                     │
│ (.scratch/tickets/04-runner-tests-and-conformance.md)  │
└────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | WorkflowRunner Constructor Auto-Wiring & Reset | `app/rag/workflows/runner.py`, `tests/conftest.py` | None | `completed` |
| **Ticket 02** | Transport Layer Simplification (`routes.py`) | `app/api/routes.py`, `tests/api/test_routes_multi_workflow.py` | Ticket 01 | `completed` |
| **Ticket 03** | Telemetry, Contexts & Experiment Migration | `evaluation/run_experiment.py`, `tests/test_jev_telemetry.py`, `tests/test_llm_telemetry.py`, `tests/test_retrieved_contexts.py`, `tests/test_experiment_runner.py` | Ticket 01 | `completed` |
| **Ticket 04** | WorkflowRunner Test Modernization & Conformance Gate | `tests/rag/test_workflow_runner.py`, Full pytest suite | Tickets 01, 02, 03 | `completed` |

---

## Ticket Locations

1. [Ticket 01: WorkflowRunner Constructor Auto-Wiring & Reset](../../.scratch/tickets/01-runner-constructor-and-reset.md)
2. [Ticket 02: Transport Layer Simplification](../../.scratch/tickets/02-routes-simplification.md)
3. [Ticket 03: Telemetry, Contexts & Experiment Migration](../../.scratch/tickets/03-telemetry-and-callers-migration.md)
4. [Ticket 04: WorkflowRunner Test Modernization & Conformance Gate](../../.scratch/tickets/04-runner-tests-and-conformance.md)
