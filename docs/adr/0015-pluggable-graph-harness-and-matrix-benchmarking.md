# ADR 0015: Pluggable Graph Harness and Matrix Benchmarking Engine

## Status
Accepted (Extends ADR 0007, ADR 0010, ADR 0013)

## Context
As the RAG and agent platform evolves, the visual creation, YAML/DSL parsing, and dynamic compilation of LangGraph graphs are being decoupled into an external studio/project. The `rag-engine` repository must serve as an enterprise-grade, headless "Evaluation, Execution & Benchmarking Harness".

Prior to this decision:
1. **Coupling to Transport Schemas**: `WorkflowDefinition` in `app/rag/workflows/base.py` and workflow implementations relied directly on FastAPI transport models (`ChatRequest`, `ChatResponse`), violating the architectural rule in `docs/agents/workflow-execution.md` that domain packages (`app/rag/**`) must not import or couple to transport (`app/api/**`). This hindered running workflows in pure headless CLI/benchmark environments.
2. **Fixed Graph Packaging**: Registering new workflows required authoring custom in-repo Python classes rather than importing external pre-compiled graphs or modular bundles exported by a visual graph studio.
3. **Absence of Matrix Benchmarking**: `evaluation/run_experiment.py` supported running fixed registered workflows (`fix_single_llm`, `fix_single_jev`) against a benchmark file, but lacked the capability to run matrix evaluations (sweeping the same workflow across multiple prompt versions, temperature settings, and LLM backends).
4. **Reference-Free Only Evaluation**: The `Evaluator` protocol seam (`app/rag/evaluation/base.py`) only accepted runtime context (`question`, `answer`, `source_used`, `retrieved_contexts`) for reference-free metrics (`faithfulness`, `answer_relevancy`), without utilizing golden reference answers or expected source classifications available in benchmark datasets.
5. **Flat Observability**: Langfuse traces received simple tags (`workflow:<name>`) without structured multidimensional categorization (`variant_id`, `prompt_version`, `model_name`), making apples-to-apples comparison dashboards difficult in Langfuse v5.

---

## Decisions

### 1. Domain Boundary Inversion & Transport Decoupling (`WorkflowInput` & `WorkflowOutput`)
- Define domain-native, Pydantic DTOs `WorkflowInput` and `WorkflowOutput` in `app/rag/workflows/base.py` with zero dependencies on `app.api.*`.
- `WorkflowDefinition` protocol methods (`to_initial_state`, `to_workflow_output`, `extract_eval_context`, `handle_error`) will operate exclusively on `WorkflowInput` and `WorkflowOutput`.
- `WorkflowRunner` (at the application orchestration seam) assumes responsibility for converting between FastAPI transport models (`ChatRequest` $\leftrightarrow$ `WorkflowInput`, `WorkflowOutput` $\leftrightarrow$ `ChatResponse`), allowing both web streaming routes and headless CLI benchmarking runners to invoke workflows seamlessly.

### 2. Standard State Schema (`StandardWorkflowState`) & `PluggableWorkflow`
- Establish `StandardWorkflowState` as the canonical `TypedDict` contract for external LangGraph workflows:
  - Inbound keys: `question`, `messages`, `attachments`, `session_id`, `trace_id`.
  - Outbound keys: `answer`, `retrieved_contexts`, `source_used`, `citations`, `trace`, `rewritten_query`.
- Implement `PluggableWorkflow` in `app/rag/workflows/base.py` implementing `WorkflowDefinition` to wrap external compiled graphs with zero boilerplate:
  - Normalizes `WorkflowInput` into `StandardWorkflowState`.
  - Adapts `StandardWorkflowState` to `WorkflowOutput`, resolving the answer from `state["answer"]` or the final `AIMessage` in `state["messages"]`.
  - Normalizes evidence in `extract_eval_context` and provides graceful degradation in `handle_error`.

### 3. Manifest + Factory Packaging Convention & Dynamic Registry Loading
- Standardize external workflow export artifacts into a **Manifest + Factory Module Convention**:
  - `manifest.json`: Side-effect-free capability metadata (`id`, `name`, `description`, `entrypoint`, `is_conversational`, `supports_streaming`, `step_labels`, `generation_nodes`).
  - Python factory module: Exporting `get_graph(config: dict[str, Any] | None = None) -> CompiledStateGraph`.
- Extend `WorkflowRegistry` in `app/rag/workflows/registry.py` with:
  - `load_from_directory(path: Path | str) -> int`: Discovers and mounts external workflow bundles dynamically on startup.
  - Conformance pre-flight verification: Validates structural and streaming invariants upon registration.

### 4. `ExecutionProfile` & 3-Tier Precedence Hierarchy
- Introduce `ExecutionProfile` (`variant_id`, `model_name`, `prompt_version`, `temperature`, `parameters`, `tags`, `metadata`).
- Implement a 3-tier parameter resolution hierarchy in `WorkflowRunner._prepare_execution`:
  1. **Tier 1 (Explicit Request Overrides)**: Inbound parameters (`WorkflowInput.parameters`).
  2. **Tier 2 (Registered Profile)**: Looked up from registry via `WorkflowInput.variant_id`.
  3. **Tier 3 (Workflow Baseline)**: Manifest and factory defaults.
- Inject resolved parameters into LangGraph's `RunnableConfig["configurable"]` so graph nodes and LLM factories access them at runtime.
- Propagate resolved variants to Langfuse v5 telemetry via structured tags (`workflow:<id>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`, `env:<env>`) and metadata.

### 5. Hybrid Compilation Lifecycle
- **Approach A (Default)**: Compile graph once; nodes read dynamic parameters from `config["configurable"]` at runtime for high concurrency and zero per-request compilation latency.
- **Approach B (Fallback Cache)**: Support `workflow.get_compiled_graph(variant_id=...)` caching distinct compiled instances when external graphs require model or tool bindings at graph construction time.

### 6. Evaluator Seam Ground-Truth Support & Matrix Benchmarking
- Extend `Evaluator.evaluate(...)` and `Evaluator.aevaluate(...)` with optional keyword arguments:
  - `expected_output: str | None = None`
  - `expected_source: str | None = None`
- Compute supervised benchmark metrics (`source_accuracy`, reference-based correctness) when ground-truth fields are present, while leaving online background evaluation reference-free.
- Upgrade `evaluation/run_experiment.py` to support matrix experiment plans (YAML-driven) comparing multiple workflows, models, and prompt profiles side-by-side.
- Record experiment runs in Langfuse v5 under `<workflow_id>@<variant_id>--<timestamp>` and link dataset items to trace observations.

---

## Consequences

### Positive
- **Complete Decoupling**: Visual graph authoring occurs externally; `rag-engine` executes and benchmarks graphs headlessly with clean domain boundaries.
- **No Reverse Coupling**: Transport models (`app/api/schemas.py`) are strictly decoupled from `app/rag/workflows/`.
- **Apples-to-Apples Benchmarking**: Matrix sweeps evaluate prompt versions, LLMs, and routing strategies across identical golden datasets.
- **Telemetry Precision**: Langfuse v5 traces, dataset runs, and evaluator scores are organized cleanly by `workflow_id` and `variant_id`.

### Negative
- **DTO Translation Layer**: Introducing `WorkflowInput` and `WorkflowOutput` adds a lightweight translation step in `WorkflowRunner`.
- **Dynamic Import Governance**: Loading external Python code from a directory requires directory validation and safe error handling.
