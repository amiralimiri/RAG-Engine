# Work Packages / Implementation Tickets: Unified BaseTool Abstraction Seam

- **Specification Reference:** [`docs/specs/0008-unified-basetool-abstraction-seam-spec.md`](./0008-unified-basetool-abstraction-seam-spec.md)
- **ADR Reference:** [`docs/adr/0008-unified-basetool-abstraction-seam.md`](../adr/0008-unified-basetool-abstraction-seam.md)
- **Status:** Ready for Implementation

---

## Dependency Graph

```
┌───────────────────────────────────────────────┐       ┌───────────────────────────────────────────────┐
│ Ticket 01: Retriever Tool Dual-Output         │       │ Ticket 02: Web Search Tool Dual-Output        │
│ Artifact & Error Propagation                  │       │ Artifact & Error Propagation                  │
│ (app/rag/tools/retriever.py)                  │       │ (app/rag/tools/search.py)                     │
└───────────────────────┬───────────────────────┘       └───────────────────────┬───────────────────────┘
                        │                                                       │
                        └───────────────────────────┬───────────────────────────┘
                                                    │
                                                    ▼
                        ┌───────────────────────────────────────────────────────┐
                        │ Ticket 03: Workflow Engine Tool Injection             │
                        │ & Async Node Promotion                                │
                        │ (app/rag/engine.py)                                   │
                        └───────────────────────────┬───────────────────────────┘
                                                    │
                                                    ▼
                        ┌───────────────────────────────────────────────────────┐
                        │ Ticket 04: Parameterize SelfCorrectiveRagWorkflow     │
                        │ with Tool Seam                                        │
                        │ (app/rag/workflows/self_corrective.py)                │
                        └───────────────────────────┬───────────────────────────┘
                                                    │
                                                    ▼
                        ┌───────────────────────────────────────────────────────┐
                        │ Ticket 05: End-to-End Streaming & Full Regression     │
                        │ Verification Suite                                    │
                        │ (tests/test_chat_stream.py, full pytest suite)        │
                        └───────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Retriever Tool Dual-Output Artifact & Error Propagation | `app/rag/tools/retriever.py`, `tests/rag/test_tools.py` | None | `completed` |
| **Ticket 02** | Web Search Tool Dual-Output Artifact & Error Propagation | `app/rag/tools/search.py`, `tests/rag/test_tools.py` | None | `completed` |
| **Ticket 03** | Workflow Engine Tool Injection & Async Node Promotion | `app/rag/engine.py`, `tests/test_workflow_engine.py` | Tickets 01, 02 | `completed` |
| **Ticket 04** | Parameterize `SelfCorrectiveRagWorkflow` with Tool Seam | `app/rag/workflows/self_corrective.py`, `tests/rag/test_self_corrective_contract.py` | Ticket 03 | `completed` |
| **Ticket 05** | End-to-End Streaming & Full Regression Suite | `tests/test_chat_stream.py`, full suite | Ticket 04 | `ready-for-agent` |
