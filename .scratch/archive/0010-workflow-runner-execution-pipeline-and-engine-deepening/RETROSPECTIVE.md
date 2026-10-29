# Retrospective: Milestone 0010 (WorkflowRunner Execution Pipeline & Engine Deepening)

**Date:** October 29, 2026  
**Status:** Completed & Archived  
**ADR Reference:** `docs/adr/0010-workflow-runner-execution-pipeline-and-engine-deepening.md`  
**Specification:** `docs/specs/0010-workflow-runner-execution-pipeline-and-engine-deepening-spec.md`  
**Test Suite:** 477 passed, 0 failed, 100% offline-capable  

---

## 1. Executive Summary & Accomplishments

Milestone 0010 centralized workflow invocation, token streaming, telemetry context binding, and asynchronous online evaluation into a deep domain module: `WorkflowRunner` (`app/rag/workflows/runner.py`). This eliminated massive procedural duplication across the HTTP transport layer and legacy shims while cutting `app/api/routes.py` from 416 lines down to 119 lines.

### Deliverables:
- **`WorkflowRunner` Deep Execution Pipeline (`app/rag/workflows/runner.py`)**:
  - Unified synchronous (`execute`, `execute_with_state`) and native asynchronous (`aexecute`, `aexecute_with_state`) execution methods with automated telemetry contextvar binding, error fallback handling, and GC-safeguarded evaluation task scheduling.
  - Native SSE token streaming pipeline (`stream`) with LangGraph v2 event parsing, tool-call delta filtering, step tracking, error fallback emission, and terminal metadata delivery.
- **Collapsed HTTP Transport Layer (`app/api/routes.py`)**:
  - Down from 416 lines to 119 lines (< 120 line target constraint).
  - Routes strictly handle HTTP parsing, status mapping, and response streaming, delegating all domain execution to `WorkflowRunner`.
- **Isolated Legacy Compatibility Facade (`app/rag/workflows/legacy.py`)**:
  - `_LegacyUnifiedWorkflowAdapter` encapsulates legacy monkeypatched dictionaries (`WORKFLOW_GRAPHS`, `WORKFLOW_ASKS`) with cached compiled graphs and canonical initial states.
- **Pruned Workflow Engine (`app/rag/engine.py`)**:
  - Eliminated mutable global dictionaries and global execution locks.
  - Legacy shims (`engine.ask`, `fix_single_llm.ask`, `fix_single_jev.ask`) route through `WorkflowRunner.execute_with_state()` without mutating Pydantic schema instances.

---

## 2. What Went Well

1. **Drastic Reduction in Route Complexity**:
   - `app/api/routes.py` was reduced by over 70% in line count, completely shedding procedural streaming and evaluation bookkeeping.
2. **Zero Regressions Across All Milestones**:
   - All 477 tests passed across all workflows (`fix_single_llm`, `fix_single_jev`, `react_agent`), tools, parsers, classifiers, and streaming endpoints.
3. **Clean Dependency Injection**:
   - Inverted transport dependencies by allowing `WorkflowRunner` to accept injected telemetry callbacks, trace extractors, flusher hooks, and evaluation functions rather than inspecting HTTP route modules.
4. **Pydantic Immutability**:
   - Introduced `execute_with_state()` and `aexecute_with_state()` returning `(ChatResponse, final_state)` tuples, eliminating monkeypatched private attributes (`_raw_state`).

---

## 3. Friction Points & Challenges Encountered

1. **Reverse Architectural Coupling (`sys.modules.get("app.api.routes")`)**:
   - *Issue*: During early implementation, `WorkflowRunner` inspected `sys.modules.get("app.api.routes")` to retrieve test-monkeypatched hooks. This violated clean architectural layering (domain module depending on transport layer).
   - *Resolution*: Replaced all module introspection with clean constructor and method parameter injection (`callback_factory`, `trace_extractor`, `telemetry_flusher`, `eval_task_fn`), defaulting to core domain singletons.
2. **Double-Firing Callbacks on Tool Wrappers**:
   - *Issue*: When `DualOutputStructuredTool` wrapped a client that was already a LangChain `BaseTool`, invoking both caused duplicate `on_tool_start` callback invocations in unit tests.
   - *Resolution*: Handled directly patched `BaseTool` instances cleanly in `engine.search_web()` without redundant nested wrappers.
3. **Premature Registry Checks in Streaming**:
   - *Issue*: `stream()` initially performed a raw dictionary lookup in `self.registry` before `_prepare_execution()` could auto-register canonical default workflows, raising HTTP 400 prematurely.
   - *Resolution*: Centralized workflow resolution in `_resolve_workflow()` on `WorkflowRunner` to ensure consistent auto-registration across both streaming and synchronous execution.
4. **PEP 484 Syntax Violations**:
   - *Issue*: Code review flagged `config: RunnableConfig = None` across tools, which violates PEP 484 type standards.
   - *Resolution*: Enforced `config: RunnableConfig | None = None` universally.

---

## 4. Key Process Insights & Guardrails

- **Domain Never Imports Transport**: Modules under `app/rag/**` must never import from or inspect `app/api/**`. Always inject hooks at the transport seam.
- **Never Drop Callbacks**: Tool wrappers must never strip `child_config["callbacks"] = []` to avoid callback collisions. Handle seams cleanly at invocation points.
- **No Dynamic Pydantic Mutation**: Never attach ad-hoc underscore attributes (`_raw_state`) to Pydantic models. Return structured tuples or dedicated domain state objects.

---

## 5. Artifact & Ticket Archive Status

| Item | Location | Status |
|---|---|---|
| **Ticket 01** | `.scratch/archive/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/01-workflow-runner-core-execution.md` | `closed` |
| **Ticket 02** | `.scratch/archive/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/02-workflow-runner-sse-streaming.md` | `closed` |
| **Ticket 03** | `.scratch/archive/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/03-legacy-compatibility-facade.md` | `closed` |
| **Ticket 04** | `.scratch/archive/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/04-engine-pruning-legacy-modernization.md` | `closed` |
| **Ticket 05** | `.scratch/archive/0010-workflow-runner-execution-pipeline-and-engine-deepening/issues/05-http-router-collapse-regression.md` | `closed` |
| **Work Packages Doc** | `docs/specs/0010-workflow-runner-execution-pipeline-and-engine-deepening-tickets.md` | `Completed` |
| **Tech Spec** | `docs/specs/0010-workflow-runner-execution-pipeline-and-engine-deepening-spec.md` | `Completed` |
| **ADR 0010** | `docs/adr/0010-workflow-runner-execution-pipeline-and-engine-deepening.md` | `Accepted` |
