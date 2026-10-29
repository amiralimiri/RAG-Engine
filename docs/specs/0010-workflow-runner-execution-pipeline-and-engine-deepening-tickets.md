# Work Packages / Implementation Tickets: Workflow Runner Execution Pipeline and Engine Deepening

- **Specification Reference:** [`docs/specs/0010-workflow-runner-execution-pipeline-and-engine-deepening-spec.md`](./0010-workflow-runner-execution-pipeline-and-engine-deepening-spec.md)
- **ADR Reference:** [`docs/adr/0010-workflow-runner-execution-pipeline-and-engine-deepening.md`](../adr/0010-workflow-runner-execution-pipeline-and-engine-deepening.md)
- **Domain Glossary:** [`GLOSSARY.md`](../../GLOSSARY.md)
- **Status:** Completed

---

## Dependency Graph

```
┌───────────────────────────────────────────────┐       ┌───────────────────────────────────────────────┐
│ Ticket 01: WorkflowRunner Core Execution &    │       │ Ticket 02: WorkflowRunner SSE Streaming &     │
│ Unit Test Suite                               │       │ Tool Delta Filtering                          │
│ (.scratch/0010.../01-workflow-runner...md)    │       │ (.scratch/0010.../02-workflow-runner...md)    │
└───────────────────────┬───────────────────────┘       └───────────────────────┬───────────────────────┘
                        │                                                       │
                        ├───────────────────────────┬───────────────────────────┘
                        │                           │
                        ▼                           │
┌───────────────────────────────────────────────┐   │
│ Ticket 03: Legacy Compatibility Facade &      │   │
│ Test-Shim Isolation                           │   │
│ (.scratch/0010.../03-legacy-compatibility...md│   │
└───────────────────────┬───────────────────────┘   │
                        │                           │
                        ▼                           │
┌───────────────────────────────────────────────┐   │
│ Ticket 04: Engine Pruning & Legacy            │   │
│ Entrypoint Modernization                      │   │
│ (.scratch/0010.../04-engine-pruning...md)     │   │
└───────────────────────┬───────────────────────┘   │
                        │                           │
                        └───────────┬───────────────┘
                                    │
                                    ▼
        ┌───────────────────────────────────────────────────────┐
        │ Ticket 05: HTTP Router Collapse & Full E2E Streaming   │
        │ Regression Suite                              │
        │ (.scratch/0010.../05-http-router-collapse...md)       │
        └───────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | WorkflowRunner Core Execution & Unit Test Suite | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_runner.py` | None | `completed` |
| **Ticket 02** | WorkflowRunner SSE Streaming & Tool Delta Filtering | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_runner.py` | Ticket 01 | `completed` |
| **Ticket 03** | Legacy Compatibility Facade & Test-Shim Isolation | `app/rag/workflows/legacy.py`, `app/rag/workflows/__init__.py`, `tests/rag/test_legacy_compat.py` | Ticket 01 | `completed` |
| **Ticket 04** | Engine Pruning & Legacy Entrypoint Modernization | `app/rag/engine.py`, `app/rag/fix_single_llm.py`, `app/rag/fix_single_jev.py`, `tests/test_workflow_engine.py` | Tickets 01, 03 | `completed` |
| **Ticket 05** | HTTP Router Collapse & Full E2E Streaming Regression | `app/api/routes.py`, `tests/api/test_routes_multi_workflow.py`, full suite | Tickets 02, 03, 04 | `completed` |

---

## Ticket Locations

1. [Ticket 01: WorkflowRunner Core Execution & Unit Test Suite](../../.scratch/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/01-workflow-runner-core-execution.md)
2. [Ticket 02: WorkflowRunner SSE Streaming & Tool Delta Filtering](../../.scratch/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/02-workflow-runner-sse-streaming.md)
3. [Ticket 03: Legacy Compatibility Facade & Test-Shim Isolation](../../.scratch/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/03-legacy-compatibility-facade.md)
4. [Ticket 04: Engine Pruning & Legacy Entrypoint Modernization](../../.scratch/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/04-engine-pruning-legacy-modernization.md)
5. [Ticket 05: HTTP Router Collapse & Full E2E Streaming Regression](../../.scratch/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/05-http-router-collapse-regression.md)
