# Retrospective: Milestone 0015 (Pluggable Graph Harness & Matrix Benchmarking Engine)

**Date:** November 1, 2026  
**Status:** Completed, Reviewed, Remediated, & Archived  
**ADR Reference:** `docs/adr/0015-pluggable-graph-harness-and-matrix-benchmarking.md`  
**Specification:** `docs/specs/0015-pluggable-graph-harness-and-matrix-benchmarking-spec.md`  
**Work Packages / Tickets:** `docs/specs/0015-pluggable-graph-harness-and-matrix-benchmarking-tickets.md`  
**Domain Guidelines:** `docs/agents/workflow-execution.md` | `docs/agents/codebase-unification.md` | `docs/agents/evaluation-engine.md`  
**Test Suite:** 575 passed, 0 failed, 100% offline-capable, clean pyright across domain & transport packages  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0015 established a decoupled domain DTO seam, a generic pluggable workflow harness, dynamic bundle discovery with pre-flight conformance validation, 3-tier parameter resolution with multi-dimensional telemetry, and a supervised matrix benchmarking engine for offline experimentation.

Crucially, this milestone completely purged transport schema couplings (`app.api.schemas`) from the core workflow execution domain (`app/rag/workflows/`), establishing a pure zero-shim contract where `WorkflowRunner` operates strictly over domain DTOs (`WorkflowInput`, `WorkflowOutput`, and `ExecutionResult`).

### Key Deliverables:

1. **Domain DTOs & Transport Layer Decoupling (Ticket 01)**:
   - Defined pure domain models `WorkflowInput` and `WorkflowOutput` in `app/rag/workflows/base.py` with zero dependencies on `app.api.schemas`.
   - Updated `WorkflowDefinition` Protocol methods to use `WorkflowInput` and `WorkflowOutput`.
   - Purged all `app.api.schemas` (`ChatRequest`, `ChatResponse`) imports across `app/rag/workflows/runner.py`, `react_agent.py`, `self_corrective.py`, and `base.py`.
   - Moved web-to-DTO bi-directional translation to the transport route layer (`app/api/routes.py` via `ChatRequest.to_workflow_input()` and `ChatResponse.from_workflow_output()`).
   - Standardized `citations` typing to `list[dict[str, Any]]` across domain contracts and normalized legacy string citations in test doubles.

2. **Standard Workflow State & `PluggableWorkflow` Implementation (Ticket 02)**:
   - Defined canonical `StandardWorkflowState` (`TypedDict`) containing standard state keys (`question`, `messages`, `attachments`, `session_id`, `trace_id`, `answer`, `retrieved_contexts`, `source_used`, `citations`, `trace`, `rewritten_query`, `variant_id`).
   - Implemented `PluggableWorkflow` in `app/rag/workflows/base.py` to wrap arbitrary compiled LangGraph runnables or lazy factory callables (`Callable[[dict[str, Any] | None], Any]`).
   - Implemented resilient answer extraction: handles direct `state["answer"]` or trailing `AIMessage` in `state["messages"]`.
   - Implemented error handling fallback producing structured `WorkflowOutput` with error trace.
   - Validated complete protocol conformance via `BaseWorkflowContractTest` across multiple graph architectures.

3. **Dynamic Manifest + Factory Discovery in `WorkflowRegistry` (Ticket 03)**:
   - Added pre-flight conformance validation to `WorkflowRegistry.register(workflow)` (verifies `WorkflowInfo`, ID validity, and streaming node declarations).
   - Implemented `WorkflowRegistry.load_from_directory(path)` to automatically discover external workflow packages containing `manifest.json` and a Python factory entrypoint (`module:callable`).
   - Built robust error handling that logs malformed bundles and syntax errors without crashing registry initialization.

4. **`ExecutionProfile` 3-Tier Resolution & Multi-Dimensional Telemetry (Ticket 04)**:
   - Defined `ExecutionProfile` in `app/rag/workflows/base.py` for parameterized model configurations.
   - Added profile registry management (`register_profile`, `get_profile`, `list_profiles`).
   - Implemented a strict 3-tier precedence hierarchy in `WorkflowRunner._prepare_execution(...)`:
     $$\text{Explicit Request Overrides} > \text{Registered Execution Profile} > \text{Workflow Baseline Metadata}$$
   - Injected resolved parameters into LangGraph's `RunnableConfig["configurable"]`.
   - Emitted multi-dimensional Langfuse telemetry tags: `workflow:<id>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`, `env:<env>`.
   - Implemented cached per-variant compilation in `PluggableWorkflow.get_compiled_graph(variant_id=...)` passing `config: dict[str, Any] | None` to graph factories.

5. **Supervised Evaluator Seam Enhancement & Matrix Benchmarking Runner (Ticket 05)**:
   - Enhanced `Evaluator` protocol seam (`evaluate`, `aevaluate`) with optional ground-truth parameters `expected_output` and `expected_source`.
   - Implemented discrete `source_accuracy` scoring (1.0 on match, 0.0 on mismatch) and reference-based correctness evaluation in `EvaluationEngine` and `FakeEvaluator`.
   - Extended `evaluation/run_experiment.py` to accept `--matrix <path.yaml>`, executing benchmark plans over multiple workflow/profile definitions.
   - Formatted benchmark runs in Langfuse v5 Datasets as `<workflow_id>@<variant_id>--<timestamp>` and generated ASCII summary comparison matrices.

---

## 2. What Went Well

1. **Clean Architectural Boundaries (Zero Transport Leakage)**:
   - Completely isolating `app/rag/workflows/` from FastAPI transport schemas eliminated circular dependencies and enabled headless benchmark execution without web framework dependencies.
2. **Resilient Protocol Duck-Typing**:
   - Duck-typing in `WorkflowRunner._to_workflow_input` allows the runner to seamlessly accept `WorkflowInput`, legacy objects with `to_workflow_input()`, or test fixtures while strictly enforcing `WorkflowInput` at type-check boundaries.
3. **Pluggable Adaptation Flexibility**:
   - `PluggableWorkflow` supports both pre-compiled LangGraph runnables and lazy factory functions with variant configuration injection, enabling external graph definitions to plug into the engine without modifying engine source code.
4. **Rigorous Review & Remediation Loop**:
   - A dedicated multi-ticket remediation pass identified and resolved all protocol discrepancies, schema couplings in SSE streaming handlers, reference correctness evaluation omissions, and Pyright type errors across 53 workflow runner unit tests.

---

## 3. Friction Points & Challenges Encountered

1. **Streaming SSE Coupling to Transport Schemas**:
   - *Problem*: `WorkflowRunner.stream()` was creating `StreamStepEvent`, `StreamTokenEvent`, and `StreamMetadataEvent` from `app.api.schemas`.
   - *Root Cause*: Early streaming implementations coupled domain event generation with FastAPI Pydantic response models.
   - *Resolution*: Serialized raw dictionaries directly in `runner.py` using `format_sse()`, completely decoupling the SSE generator from `app.api.schemas`.
2. **Citations Schema Inconsistency Across Layers**:
   - *Problem*: Older test fixtures returned lists of string filenames (`["doc.pdf"]`) while modern schema contracts required `list[dict[str, Any]]` (`[{"title": "doc.pdf"}]`).
   - *Resolution*: Standardized domain `WorkflowOutput.citations` strictly to `list[dict[str, Any]]` and normalized string citations in `MockWorkflowDefinition` and test doubles.
3. **Pluggable Graph Factory Calling Conventions**:
   - *Problem*: Graph factories for pluggable workflows needed to accept variant configurations, but some callables expected keyword arguments (`config=...`), positional dictionaries, or zero arguments.
   - *Resolution*: Implemented multi-tier invocation fallbacks in `PluggableWorkflow.get_compiled_graph`: `factory(config=config)` $\to$ `factory(config)` $\to$ `factory()`.
4. **Pyright Protocol Conformance in Tests**:
   - *Problem*: `MockWorkflow.to_initial_state` used `request: Any` instead of `input: Any`, causing protocol parameter name mismatch warnings in Pyright.
   - *Resolution*: Renamed parameter to `input` and updated runner test requests to `WorkflowInput` for pristine type checking.

---

## 4. Codified Agent Guardrails & Updated Rules

The following rules have been codified in `docs/agents/workflow-execution.md`:

1. **Strict Domain DTO Seam**:
   - `WorkflowRunner` strictly accepts `WorkflowInput` and returns `WorkflowOutput` (or `ExecutionResult` bundling `WorkflowOutput` and `EvalContext`).
   - Never import `app.api.schemas` (`ChatRequest`, `ChatResponse`) inside `app/rag/workflows/**`.
   - Route handlers in `app/api/routes.py` perform transport translation via `ChatRequest.to_workflow_input()` and `ChatResponse.from_workflow_output()`.
2. **Standard Workflow State & Pluggable Adapter**:
   - Use `PluggableWorkflow` to wrap external or dynamically loaded LangGraph instances.
   - Ensure answer extraction supports both direct `state["answer"]` strings and trailing `AIMessage` objects in `state["messages"]`.
3. **Dynamic Registry Discovery & Validation**:
   - External workflows are packaged with `manifest.json` and a Python factory entrypoint (`module:function`).
   - Pre-flight validation on `WorkflowRegistry.register()` must verify metadata validity, non-empty IDs, and streaming generation node alignment.
4. **Three-Tier Precedence Hierarchy**:
   - Execution parameters resolve as: Explicit Request Overrides > Registered `ExecutionProfile` > Workflow Baseline metadata.
   - Resolved parameters inject into `RunnableConfig["configurable"]`.
5. **Multi-Dimensional Telemetry Attribution**:
   - Always tag Langfuse traces with canonical tags: `workflow:<id>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`, `env:<env>`.
6. **Supervised Evaluator Seam & Matrix Benchmarking**:
   - Pass `expected_output` and `expected_source` through `Evaluator.evaluate()` / `aevaluate()`.
   - Format matrix dataset experiment runs in Langfuse as `<workflow_id>@<variant_id>--<timestamp>`.

---

## 5. Artifact & Ticket Archive Status

All 5 work packages have been archived to `.scratch/archive/0015-pluggable-graph-harness-and-matrix-benchmarking/issues/`:

| Ticket | Scope | Status |
|---|---|---|
| **Ticket 01** | Domain DTOs (`WorkflowInput`, `WorkflowOutput`) & Transport Decoupling | Completed & Archived |
| **Ticket 02** | `StandardWorkflowState` & `PluggableWorkflow` Implementation | Completed & Archived |
| **Ticket 03** | Dynamic Manifest + Factory Discovery in `WorkflowRegistry` | Completed & Archived |
| **Ticket 04** | `ExecutionProfile` 3-Tier Resolution & Multi-Dimensional Telemetry | Completed & Archived |
| **Ticket 05** | Supervised Evaluator Seam Enhancement & Matrix Benchmarking Runner | Completed & Archived |

Active scratch directory `.scratch/0015-pluggable-graph-harness-and-matrix-benchmarking/` has been pruned and moved into `.scratch/archive/`.

---

## 6. Future Architectural Opportunities

1. **Declarative Hot-Reloading for Pluggable Workflows**:
   - Implement file-watching on the pluggable workflow directory (`app/rag/workflows/pluggable/`) to hot-reload manifests without restarting the server.
2. **Persistent Execution Profile Storage**:
   - Support persisting execution profiles in a relational or SQLite store for dynamic management via administrative APIs.
3. **Automated Matrix Hyperparameter Tuning**:
   - Extend `evaluation/run_experiment.py` with grid search or Bayesian optimization to automatically discover optimal temperature, top-$k$, and prompt variants.
