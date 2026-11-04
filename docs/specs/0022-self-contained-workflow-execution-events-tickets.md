# Work Packages / Implementation Tickets: Self-Contained Workflow Execution Events and Pure Transport Adapters

- **Specification Reference:** [`docs/specs/0022-self-contained-workflow-execution-events-spec.md`](./0022-self-contained-workflow-execution-events-spec.md)
- **ADR Reference:** [`docs/adr/0022-self-contained-workflow-execution-events.md`](../adr/0022-self-contained-workflow-execution-events.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/workflow-execution.md`](../agents/workflow-execution.md)
- **Status:** Ready for Execution (4 Sequential Tickets)
- **Triage Label:** `ready-for-agent`

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 01: Self-Contained StepTransitionEvent & Domain Event Tests     │
│ (issues/01-self-contained-step-transition-event.md)                    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 02: Runner Step Label Resolution at Emission Seam               │
│ (issues/02-runner-step-label-resolution.md)                            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 03: Pure SseStreamAdapter & Presentation Test Alignment         │
│ (issues/03-pure-sse-stream-adapter.md)                                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Ticket 04: Collapse Presentation Route & Regression Verification       │
│ (issues/04-collapse-presentation-route-and-regression.md)              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Self-Contained StepTransitionEvent & Domain Event Tests | `app/rag/workflows/events.py`, `tests/rag/test_workflow_events.py` | None | `ready-for-agent` |
| **Ticket 02** | Runner Step Label Resolution at Emission Seam | `app/rag/workflows/runner.py`, `tests/rag/test_workflow_runner.py` | Ticket 01 | `ready-for-agent` |
| **Ticket 03** | Pure SseStreamAdapter & Presentation Test Alignment | `app/api/streaming.py`, `tests/api/test_sse_stream_adapter.py` | Ticket 02 | `ready-for-agent` |
| **Ticket 04** | Collapse Presentation Route & Regression Verification | `app/api/routes.py`, `tests/test_chat_stream.py`, full regression suite | Ticket 03 | `ready-for-agent` |

---

## Ticket Locations

1. [Ticket 01: Self-Contained StepTransitionEvent & Domain Event Tests](./0022-self-contained-workflow-execution-events/issues/01-self-contained-step-transition-event.md)
2. [Ticket 02: Runner Step Label Resolution at Emission Seam](./0022-self-contained-workflow-execution-events/issues/02-runner-step-label-resolution.md)
3. [Ticket 03: Pure SseStreamAdapter & Presentation Test Alignment](./0022-self-contained-workflow-execution-events/issues/03-pure-sse-stream-adapter.md)
4. [Ticket 04: Collapse Presentation Route & Regression Verification](./0022-self-contained-workflow-execution-events/issues/04-collapse-presentation-route-and-regression.md)
