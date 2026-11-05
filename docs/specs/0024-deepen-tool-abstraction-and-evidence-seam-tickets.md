# Work Packages / Implementation Tickets: Deepen Tool Abstraction and Evidence Seam

- **Specification Reference:** [`docs/specs/0024-deepen-tool-abstraction-and-evidence-seam-spec.md`](./0024-deepen-tool-abstraction-and-evidence-seam-spec.md)
- **ADR Reference:** [`docs/adr/0024-deepen-tool-abstraction-and-evidence-seam.md`](../adr/0024-deepen-tool-abstraction-and-evidence-seam.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/tool-abstractions.md`](../agents/tool-abstractions.md)
- **Status:** Ready for Execution
- **Triage Label:** `ready-for-agent`

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 01: Canonical Domain Models & Polymorphic Evidence Aggregator   │
│ (issues/01-canonical-domain-models-and-polymorphic-aggregator.md)      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 02: RAGTool Execution Seam, Concrete Adapters & Zero-Shim Pruning│
│ (issues/02-rag-tool-execution-seam-adapters-and-zero-shim-pruning.md)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 03: Workflow Engine & ReAct Agent Integration with Regression   │
│ (issues/03-workflow-engine-react-agent-integration-and-regression.md) │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Canonical Domain Models & Polymorphic Evidence Aggregator | `app/rag/tools/base.py`, `tests/rag/test_tools.py` | None | `closed` |
| **Ticket 02** | RAGTool Execution Seam, Concrete Adapters & Zero-Shim Pruning | `app/rag/tools/base.py`, `app/rag/tools/retriever.py`, `app/rag/tools/search.py`, `app/rag/tools/__init__.py`, `tests/rag/test_tools.py` | Ticket 01 | `closed` |
| **Ticket 03** | Workflow Engine & ReAct Agent Integration with Regression Verification | `app/rag/engine.py`, `app/rag/workflows/react_agent.py`, `tests/rag/test_react_agent.py`, `tests/rag/test_base_workflow.py`, full regression suite | Ticket 02 | `closed` |

---

## Ticket Locations

1. [Ticket 01: Canonical Domain Models & Polymorphic Evidence Aggregator](./0024-deepen-tool-abstraction-and-evidence-seam/issues/01-canonical-domain-models-and-polymorphic-aggregator.md)
2. [Ticket 02: RAGTool Execution Seam, Concrete Adapters & Zero-Shim Pruning](./0024-deepen-tool-abstraction-and-evidence-seam/issues/02-rag-tool-execution-seam-adapters-and-zero-shim-pruning.md)
3. [Ticket 03: Workflow Engine & ReAct Agent Integration with Regression Verification](./0024-deepen-tool-abstraction-and-evidence-seam/issues/03-workflow-engine-react-agent-integration-and-regression.md)
