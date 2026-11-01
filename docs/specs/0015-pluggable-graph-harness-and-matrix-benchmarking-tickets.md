# Tickets: Pluggable Graph Harness and Matrix Benchmarking Engine

- **Specification Reference:** `docs/specs/0015-pluggable-graph-harness-and-matrix-benchmarking-spec.md`
- **ADR Reference:** `docs/adr/0015-pluggable-graph-harness-and-matrix-benchmarking.md`
- **Target Version:** `0.15.0`
- **Status:** Approved / In Progress

---

## Ticket Overview

| Ticket | Title | Blocked By | Status |
| :--- | :--- | :--- | :--- |
| **01** | Domain DTOs (`WorkflowInput`, `WorkflowOutput`) & Transport Decoupling | None | Completed |
| **02** | `StandardWorkflowState` & `PluggableWorkflow` Implementation | 01 | Ready |
| **03** | Dynamic Manifest + Factory Discovery in `WorkflowRegistry` | 02 | Pending |
| **04** | `ExecutionProfile` 3-Tier Resolution & Multi-Dimensional Telemetry | 01 | Ready |
| **05** | Supervised Evaluator Seam Enhancement & Matrix Benchmarking Runner | 04 | Pending |

---

## Ticket Details

### Ticket 01: Domain DTOs (`WorkflowInput`, `WorkflowOutput`) & Transport Decoupling
- Define `WorkflowInput` and `WorkflowOutput` Pydantic models in `app/rag/workflows/base.py`.
- Remove all imports of `app.api.schemas` (`ChatRequest`, `ChatResponse`) from `app/rag/workflows/`.
- Update `WorkflowDefinition` Protocol methods to use `WorkflowInput` and `WorkflowOutput`.
- Update `WorkflowRunner` to handle conversion between `ChatRequest`/`ChatResponse` and domain DTOs.
- Update `SelfCorrectiveRagWorkflow` and `ReActAgentWorkflow` to implement the new method signatures.
- Update `tests/rag/test_workflow_contracts.py` and ensure existing tests pass.

### Ticket 02: `StandardWorkflowState` & `PluggableWorkflow` Implementation
- Define `StandardWorkflowState` (`TypedDict`) in `app/rag/workflows/base.py`.
- Implement `PluggableWorkflow` in `app/rag/workflows/base.py` wrapping any compiled LangGraph runnable or lazy factory.
- Implement robust answer extraction (from `state["answer"]` or trailing `AIMessage`), evidence extraction, and fallback error handling.
- Add test suites in `tests/rag/test_pluggable_workflow.py` validating conformance using `BaseWorkflowContractTest`.

### Ticket 03: Dynamic Manifest + Factory Discovery in `WorkflowRegistry`
- Implement pre-flight conformance validation on `WorkflowRegistry.register()`.
- Implement `WorkflowRegistry.load_from_directory(path: Path | str) -> int` to discover packages containing `manifest.json` and a Python factory entrypoint (`module:get_graph`).
- Add tests in `tests/rag/test_registry_loader.py` validating discovery, loading, and error handling for malformed bundles.

### Ticket 04: `ExecutionProfile` 3-Tier Resolution & Multi-Dimensional Telemetry
- Define `ExecutionProfile` in `app/rag/workflows/base.py`.
- Support profile registration in `WorkflowRegistry` (`register_profile`, `get_profile`).
- Add `variant_id` and `parameters` to `ChatRequest` (optional) and map to `WorkflowInput`.
- Implement 3-tier precedence in `WorkflowRunner._prepare_execution(...)`: Explicit Overrides > Registered Profile > Workflow Baseline.
- Inject resolved parameters into `RunnableConfig["configurable"]`.
- Attach multi-dimensional Langfuse tags (`workflow:<id>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`) and metadata.
- Support Approach B (cached per-variant compilation) in `PluggableWorkflow.get_compiled_graph(variant_id=...)`.
- Add tests in `tests/rag/test_workflow_runner.py` verifying configuration injection and telemetry propagation.

### Ticket 05: Supervised Evaluator Seam Enhancement & Matrix Benchmarking Runner
- Extend `Evaluator.evaluate(...)` and `aevaluate(...)` with optional `expected_output` and `expected_source`.
- Update `EvaluationEngine` and `FakeEvaluator` to score `source_accuracy` and reference-based answer correctness.
- Update `evaluation/run_experiment.py` to support `--matrix <path.yaml>`.
- Format matrix summary comparison tables and link Langfuse v5 Dataset experiment runs (`<workflow_id>@<variant_id>--<timestamp>`).
- Add tests in `tests/test_experiment_runner.py` covering matrix plan execution with `FakeEvaluator`.
