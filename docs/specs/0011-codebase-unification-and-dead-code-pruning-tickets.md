# Work Packages / Implementation Tickets: Codebase Unification, Dead-Code Pruning, and Test Suite Modernization

- **Specification Reference:** [`docs/specs/0011-codebase-unification-and-dead-code-pruning-spec.md`](./0011-codebase-unification-and-dead-code-pruning-spec.md)
- **ADR Reference:** [`docs/adr/0011-codebase-unification-and-dead-code-pruning.md`](../adr/0011-codebase-unification-and-dead-code-pruning.md)
- **Domain Guidelines:** [`docs/agents/codebase-unification.md`](../agents/codebase-unification.md) | [`GLOSSARY.md`](../../GLOSSARY.md)
- **Status:** Proposed

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│ Ticket 01: Test Isolation Fixture Foundation           │
│ (.scratch/0011.../01-test-isolation-fixture...md)      │
└───────────┬────────────────────────────┬───────────────┘
            │                            │
            ▼                            ▼
┌──────────────────────────────┐ ┌────────────────────────────────────────────────┐
│ Ticket 02: Ingestion &       │ │ Ticket 03: Pure Engine Factory &               │
│ Storage Facade Pruning       │ │ Tool Decoupling                                │
│ (.scratch/0011.../02-canon...│ │ (.scratch/0011.../03-pure-engine-factory...md) │
└──────────────────────────────┘ └───────────────────────┬────────────────────────┘
                                                         │
                                                         ▼
                                 ┌────────────────────────────────────────────────┐
                                 │ Ticket 04: Legacy Workflow Shims Deletion      │
                                 │ (.scratch/0011.../04-legacy-workflow-shims...md│
                                 └───────────────┬────────────────┬───────────────┘
                                                 │                │
                         ┌───────────────────────┘                └───────────────────────┐
                         ▼                                                                ▼
┌────────────────────────────────────────────────┐        ┌────────────────────────────────────────────────┐
│ Ticket 05: Route & Observability Test Suite     │        │ Ticket 06: Telemetry, Contexts & Spans Test    │
│ Modernization                                  │        │ Suite Consolidation                            │
│ (.scratch/0011.../05-route-observability...md) │        │ (.scratch/0011.../06-telemetry-contexts...md)  │
└────────────────────────┬───────────────────────┘        └───────────────────────┬────────────────────────┘
                         │                                                        │
                         └───────────────────────┬────────────────────────────────┘
                                                 │
                                                 ▼
                         ┌────────────────────────────────────────────────┐
                         │ Ticket 07: Full Verification & E2E Regression  │
                         │ (.scratch/0011.../07-full-verification...md)   │
                         └────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Test Isolation Fixture Foundation | `tests/conftest.py` | None | `open` |
| **Ticket 02** | Canonical Ingestion & Storage Facade Pruning | `app/rag/knowledge/parsers.py`, `app/rag/knowledge/__init__.py`, `app/api/routes.py`, `app/services/ingestion.py` (DEL), `app/rag/vectorstore.py` (DEL), `tests/test_indexing_regression.py` (DEL) | Ticket 01 | `open` |
| **Ticket 03** | Pure Engine Factory & Tool Decoupling | `app/rag/engine.py`, `app/rag/workflows/self_corrective.py`, `app/rag/tools/search.py`, `tests/test_workflow_engine.py` | Ticket 01 | `open` |
| **Ticket 04** | Legacy Workflow Shims & Dictionary Deletion | `app/rag/workflows/legacy.py` (DEL), `app/rag/fix_single_llm.py` (DEL), `app/rag/fix_single_jev.py` (DEL), `tests/rag/test_legacy_compat.py` (DEL), `app/api/routes.py`, `app/rag/workflows/__init__.py`, `app/rag/workflows/runner.py`, `evaluation/run_experiment.py` | Tickets 01, 03 | `open` |
| **Ticket 05** | Route & Observability Test Suite Modernization | `tests/test_chat_stream.py`, `tests/test_frontend_streaming.py`, `tests/test_e2e_observability.py`, `tests/test_feedback_api.py`, `tests/test_online_evaluation.py`, `tests/unit/test_logging.py`, `tests/test_frontend_integration.py` | Tickets 01, 04 | `open` |
| **Ticket 06** | Telemetry, Contexts & Spans Test Suite Consolidation | `tests/test_retrieved_contexts.py`, `tests/test_retriever_search_spans.py`, `tests/test_llm_telemetry.py`, `tests/test_jev_telemetry.py` | Tickets 01, 03, 04 | `open` |
| **Ticket 07** | Full Verification & E2E Regression Gate | Full repository audit, `uv run pytest -v` | Tickets 01–06 | `open` |

---

## Ticket Locations

1. [Ticket 01: Test Isolation Fixture Foundation](../../.scratch/0011-codebase-unification-and-dead-code-pruning/issues/01-test-isolation-fixture-foundation.md)
2. [Ticket 02: Canonical Ingestion & Storage Facade Pruning](../../.scratch/0011-codebase-unification-and-dead-code-pruning/issues/02-canonical-ingestion-facade-pruning.md)
3. [Ticket 03: Pure Engine Factory & Tool Decoupling](../../.scratch/0011-codebase-unification-and-dead-code-pruning/issues/03-pure-engine-factory-and-tool-decoupling.md)
4. [Ticket 04: Legacy Workflow Shims & Dictionary Deletion](../../.scratch/0011-codebase-unification-and-dead-code-pruning/issues/04-legacy-workflow-shims-deletion.md)
5. [Ticket 05: Route & Observability Test Suite Modernization](../../.scratch/0011-codebase-unification-and-dead-code-pruning/issues/05-route-observability-test-modernization.md)
6. [Ticket 06: Telemetry, Contexts & Spans Test Suite Consolidation](../../.scratch/0011-codebase-unification-and-dead-code-pruning/issues/06-telemetry-contexts-spans-test-consolidation.md)
7. [Ticket 07: Full Verification & E2E Regression Gate](../../.scratch/0011-codebase-unification-and-dead-code-pruning/issues/07-full-verification-regression-gate.md)
