# 02: End-to-End Workflow Engine & Agent Graph State Typing

**What to build:** Citation metadata produced by tools flows through the entire workflow graph as typed `Citation` objects. Fixed DAG nodes and the autonomous ReAct agent both hand `WorkflowOutput` a `list[Citation]` without degrading it into dictionaries, so a caller inspecting a workflow result always receives typed citations regardless of which topology produced them.

**Blocked by:** 01 — Relocate Citation Domain Model, Boundary Coercion & Zero-Shim Invariants

**Status:** ready-for-agent

- [ ] Graph state declares its citations as a sequence of `Citation`, and the initial state seeds an empty typed sequence.
- [ ] The knowledge-base retrieval node stores the tool artifact's citations directly, with no dictionary conversion step.
- [ ] Direct-answer and insufficient-evidence nodes return empty typed citation collections.
- [ ] The self-corrective workflow's output conversion passes graph-state citations through as typed models without re-serializing them.
- [ ] The ReAct agent workflow's output conversion passes aggregated evidence citations through as typed models without re-serializing them.
- [ ] A workflow run that retrieves knowledge-base evidence yields citations that are `Citation` instances, not dictionaries.
- [ ] A ReAct agent run that invokes tools yields citations that are `Citation` instances, not dictionaries.
- [ ] Contract tests for both workflows assert typed citation instances and pass end to end.
