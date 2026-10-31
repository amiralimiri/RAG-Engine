# Work Packages / Implementation Tickets: Consolidate Evaluation Engine behind an Evaluator Seam

- **Specification Reference:** [`docs/specs/0013-consolidate-evaluation-engine-behind-evaluator-seam-spec.md`](./0013-consolidate-evaluation-engine-behind-evaluator-seam-spec.md)
- **ADR Reference:** [`docs/adr/0013-consolidate-evaluation-engine-behind-evaluator-seam.md`](../adr/0013-consolidate-evaluation-engine-behind-evaluator-seam.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/evaluation-engine.md`](../agents/evaluation-engine.md) | [`docs/agents/workflow-execution.md`](../agents/workflow-execution.md)
- **Status:** Completed

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│ Ticket 01: Core Evaluator Seam and Lazy Judge Lifecycle│
│ (.scratch/archive/0013-.../issues/01-core-evaluator...)│
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 02: WorkflowRunner Execution Context & Evaluator│
│ Injection                                              │
│ (.scratch/archive/0013-.../issues/02-workflow-runner..)│
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 03: Experiment Runner Migration & Metric        │
│ Consolidation                                          │
│ (.scratch/archive/0013-.../issues/03-experiment-runner)│
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 04: Zero-Shim Pruning of Legacy Files & Caller  │
│ Modernization                                          │
│ (.scratch/archive/0013-.../issues/04-prune-legacy-...) │
└────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Core Evaluator Seam & Lazy Judge Lifecycle | `app/rag/evaluation/base.py`, `app/rag/evaluation/judge.py`, `app/rag/evaluation/evaluator.py`, `app/rag/evaluation/__init__.py`, `tests/conftest.py`, `tests/rag/test_evaluation_engine.py` | None | `completed` |
| **Ticket 02** | WorkflowRunner Execution Context & Evaluator Injection | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_runner.py` | Ticket 01 | `completed` |
| **Ticket 03** | Experiment Runner Migration & Metric Consolidation | `evaluation/run_experiment.py`, `tests/test_experiment_runner.py` | Ticket 02 | `completed` |
| **Ticket 04** | Zero-Shim Pruning of Legacy Files & Modernization | `app/api/routes.py`, delete `app/services/evaluation.py`, delete `evaluation/judge.py`, update all test imports, full pytest regression | Ticket 03 | `completed` |

---

## Ticket Locations (Archived)

1. [Ticket 01: Core Evaluator Seam and Lazy Judge Lifecycle](../../.scratch/archive/0013-consolidate-evaluation-engine-behind-evaluator-seam/issues/01-core-evaluator-seam.md)
2. [Ticket 02: WorkflowRunner Execution Context & Evaluator Injection](../../.scratch/archive/0013-consolidate-evaluation-engine-behind-evaluator-seam/issues/02-workflow-runner-context-and-evaluator-injection.md)
3. [Ticket 03: Experiment Runner Migration & Metric Consolidation](../../.scratch/archive/0013-consolidate-evaluation-engine-behind-evaluator-seam/issues/03-experiment-runner-migration.md)
4. [Ticket 04: Zero-Shim Pruning of Legacy Files & Caller Modernization](../../.scratch/archive/0013-consolidate-evaluation-engine-behind-evaluator-seam/issues/04-prune-legacy-files-and-caller-modernization.md)
