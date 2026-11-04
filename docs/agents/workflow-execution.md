# Workflow Execution & Pipeline Seam Patterns

Guidelines and lessons learned from ADR-0010 (Workflow Runner Execution Pipeline & Engine Deepening) for orchestrating LangGraph workflows, token streaming, telemetry context binding, and online evaluation.

---

## 1. Core Principles

1. **Direction of Dependencies (No Reverse Coupling)**:
   - Domain modules (`app/rag/**`) must **never** import from, inspect, or couple to the transport/API layer (`app/api/**`).
   - If tests or transport handlers need custom telemetry, callback hooks, or background evaluation tasks, inject them via constructor parameters on `WorkflowRunner` or factory overrides in `get_workflow_runner(...)` (e.g. `evaluator`, `callback_factory`, `trace_extractor`, `telemetry_flusher`), defaulting to canonical domain adapters.

2. **PEP 484 Standards Compliance**:
   - Always type-hint optional parameters with explicit union syntax: `config: RunnableConfig | None = None`.
   - Never write `config: RunnableConfig = None` (prohibited under PEP 484).

3. **No Dynamic Pydantic Model Mutation & Encapsulated Evaluation Context**:
   - Never dynamically attach private attributes to Pydantic models (e.g. `chat_response._raw_state = final_state`).
   - The public execution seam returns standardized domain models: `execute` and `aexecute` return `WorkflowOutput`, while `stream` returns `AsyncGenerator[WorkflowExecutionEvent, None]` (per ADR-0019). Transport-specific wire adapters (such as `SseStreamAdapter` in `app/api/streaming.py`) convert domain event streams into HTTP SSE text frames.
   - For offline evaluation harnesses, benchmark experiments, or diagnostic suites requiring normalized retrieval context and evidence, use `runner.execute_with_context(request) -> ExecutionResult` (per ADR-0013). This bundles the generated response with `EvalContext` without exposing raw internal graph state or piercing private methods (`_execute_with_state`).
   - Public transport routes strictly consume `execute()` or `stream()`.

4. **Preserve Child Tool Callbacks**:
   - Never strip callbacks (`child_config["callbacks"] = []`) inside tool wrappers to suppress duplicate firing.
   - Address seam differences at the tool dispatch boundary (e.g. invoking directly patched `BaseTool` instances cleanly in engine nodes) so Langfuse and OpenTelemetry callback spans propagate uninterrupted.

5. **Centralized Workflow Resolution**:
   - Always resolve workflows through `WorkflowRunner._resolve_workflow()` so that auto-registration of canonical default workflows (`fix_single_llm`, `fix_single_jev`, `react_agent`) is identical across all entrypoints (`execute`, `aexecute`, `stream`). Never perform raw registry dictionary lookups that bypass default registration.

6. **Streaming Error Handling & Lifecycle Shielding (ADR-0019)**:
   - When an unhandled exception occurs in `WorkflowRunner.stream()`, invoke `workflow_def.handle_error(exc, state)` for error logging/cleanup, notify registered `ExecutionLifecycleListener.on_error(request, exc)`, and yield a discrete domain `ExecutionFailedEvent(error=str(exc))` rather than emitting synthetic fallback answer tokens. The presentation layer (`SseStreamAdapter`) translates this into an SSE `error` frame.

7. **Autonomous ReAct Agent & `create_agent` Seam Standards (ADR-0014)**:
   - Modernize agent workflows to compile via `from langchain.agents import create_agent` rather than deprecated `langgraph.prebuilt.create_react_agent`.
   - Pass instructions to `create_agent` using `system_prompt=...` (not `prompt=...`).
   - Node identifiers in `create_agent` default to `"model"` for reasoning and `"tools"` for execution.
   - Dual-map generation nodes (`DEFAULT_REACT_GENERATION_NODES = {"agent", "model"}`) and step labels (`DEFAULT_REACT_STEP_LABELS`) in workflow metadata to preserve SSE token streaming and step updates across both modern topologies and mock test fixtures.
   - Resilient query fallback: When converting graph states to `ChatResponse`, fall back to the latest `HumanMessage` in conversation history if `final_state.get("question")` is omitted by the agent state schema.
   - Harmonized evidence attribution: Evidence source resolution (`source_used`) must strictly align across `to_chat_response` and `extract_eval_context` using a dedicated helper (`_resolve_source_used(tools_called=...)`).
   - Tool message identification: Consolidate duck-typed message checking using a private helper `_is_tool_message(m)` (`isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool"`).

8. **Pluggable Graph Harness, Domain DTO Decoupling & Matrix Benchmarking (ADR-0015)**:
   - **Strict Domain DTO Seam**: `WorkflowRunner` strictly accepts `WorkflowInput` and returns `WorkflowOutput` (or `ExecutionResult` bundling `WorkflowOutput` and `EvalContext`). No `app.api.schemas` imports allowed in `app/rag/workflows/**`. Transport routes (`app/api/routes.py`) perform bi-directional translation via `ChatRequest.to_workflow_input()` and `ChatResponse.from_workflow_output()`.
   - **Standard Workflow State & Pluggable Adapter**: `PluggableWorkflow` wraps arbitrary compiled LangGraph graphs or lazy graph factory callables, adapting `WorkflowInput` $\to$ `StandardWorkflowState` $\to$ `WorkflowOutput`. Answer extraction robustly falls back to trailing `AIMessage` in `state["messages"]` when `state["answer"]` is absent.
   - **Dynamic Registry Discovery & Pre-flight Validation**: `WorkflowRegistry.register()` performs strict pre-flight validation on workflow metadata (IDs, generation nodes, streaming declarations). `WorkflowRegistry.load_from_directory()` discovers external workflow bundles via `manifest.json` and factory entrypoints (`module:function`), registering them as `PluggableWorkflow` instances.
   - **Three-Tier Precedence Hierarchy**: Execution parameters resolve through a 3-tier hierarchy: Explicit Overrides > Registered `ExecutionProfile` > Workflow Baseline metadata. Resolved parameters inject into `RunnableConfig["configurable"]`.
   - **Multi-Dimensional Telemetry Attribution**: Tags are formatted and propagated to Langfuse: `workflow:<id>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`, `env:<env>` along with structured profile metadata.
   - **Supervised Evaluator Seam & Matrix Benchmarking**: `Evaluator.evaluate()` and `aevaluate()` support ground-truth evaluation via `expected_output` (reference semantic correctness) and `expected_source` (discrete source accuracy). Matrix benchmarking runs via `evaluation/run_experiment.py --matrix <path.yaml>`, attributing dataset experiment runs as `<workflow_id>@<variant_id>--<timestamp>`.

9. **Unified Execution Lifecycle & Side-Effect Dispatch (ADR-0020)**:
   - **Unified Listener Seam Across All Execution Modes**: All workflow execution entry points (`execute()`, `execute_with_context()`, `aexecute()`, and `stream()`) route post-execution side-effects (conversation turn persistence and background judge evaluation) exclusively through the `ExecutionLifecycleListener` protocol seam (`self.listeners`).
   - **Zero Bypass Anti-Pattern**: Internal private bypass methods (`_apersist_turn`, `_persist_turn_sync`, and `_safe_evaluate`) are permanently deleted. Side-effects must never be executed via ad-hoc private runner methods.
   - **Fail-Open Fault Isolation**: Lifecycle dispatch helpers (`_adispatch_listeners` and `_dispatch_listeners` via `run_async_safely`) sequentially await listeners in defensive `try/except` blocks. Failures in persistence or evaluation log warnings and fail open, ensuring user response delivery is never blocked or aborted by downstream observers.
   - **Error Lifecycle Isolation & Single-Dispatch Invariant**: When graph execution fails, `on_error(request, exc)` is dispatched across registered listeners. Execution pipelines must maintain an `error_dispatched` guard per request to prevent double `on_error` emissions if subsequent fallback handling or finalization also throws.
   - **Pure Python Transport-Agnostic Execution Interface**: Workflow execution interfaces (`execute`, `execute_with_context`, `aexecute`, `stream`) are pure Python domain methods completely decoupled from web transport frameworks. Starlette/FastAPI `BackgroundTasks` parameters are prohibited in domain signatures. Background evaluation tasks are scheduled directly on active or persistent concurrency event loops (`asyncio.create_task`) with GC-shielded reference sets.
   - **Read-Path vs. Write-Path Seam Separation**: Read-path conversational history hydration is decoupled from write-path persistence. In ADR-0020, write-path turn persistence was moved exclusively to `MemoryPersistenceListener`, and in ADR-0021 read-path turn hydration was completely absorbed into `ExecutionContextBinder`.
   - **Immutable Listener Configuration & Read-Only Semantics**: `runner.listeners` and `runner.memory` are read-only properties configured during initialization. Mutable property setters (`runner.listeners = ...`, `runner.memory = ...`, `runner.evaluator = ...`, `runner.eval_task_fn = ...`) are prohibited. Headless execution or test isolation is achieved by passing `WorkflowRunner(listeners=[])` or configuring clean instances via `get_workflow_runner(...)`.
   - **Pre-Notification Context Normalization**: Heterogeneous context chunks (`dict`, `str`, objects) must be normalized to standard string lists via `WorkflowOutput.normalize_contexts()` before listener notification, ensuring evaluation listeners consume clean excerpts without reaching into raw graph or citation internals.

10. **Pre-Execution Context Binding & Typed Preparation Seam (ADR-0021)**:
    - **Dedicated Deep Binder Module (`ExecutionContextBinder`)**: Pre-execution parameter precedence resolution, conversational memory hydration, telemetry tag compilation, and structlog correlation contextvars binding are encapsulated within `ExecutionContextBinder` (`app/rag/workflows/binder.py`). `WorkflowRunner` delegates preparation to the binder via synchronous `bind()` or native asynchronous `abind()`.
    - **Immutable Domain Model (`BoundExecutionContext`)**: Pre-execution outputs bundle inside a strongly-typed, frozen domain model `BoundExecutionContext` (`frozen=True, extra="forbid"`), eliminating untyped tuple arithmetic across sync/async/streaming execution pathways. LangGraph execution loops consume `bound.as_runnable_config()`.
    - **Symmetric Memory Seam Separation**: `WorkflowRunner` is completely decoupled from direct interaction with `ConversationMemoryAdapter`. Read-path message history retrieval and rewind slicing (`rewind_turn_index`) belong exclusively to `ExecutionContextBinder`, while write-path turn persistence belongs exclusively to `MemoryPersistenceListener`. `runner.memory` is a read-only property delegating to `runner.binder.memory`.
    - **Native Async Event-Loop Hydration**: In `ExecutionContextBinder.abind()`, conversational history is awaited directly on the active event loop without unnecessary threadpool transitions (`run_async_safely`), minimizing initial chunk latency for streaming routes.
    - **Pre-Populated RunnableConfig Telemetry Tags**: Multi-dimensional telemetry tags (`workflow:<id>`, `env:<env>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`, `turn_count:<n>`) are pre-populated into `run_config["tags"]` inside `BoundExecutionContext.config` (merging order-preserving with any user-supplied tags), ensuring LangGraph nodes and downstream callbacks receive uniform telemetry metadata.
    - **Zero-Shim Direct Seam Access**: Private forwarding facades (`WorkflowRunner._to_workflow_input`) are strictly prohibited; tests and callers must interact directly with the authoritative seam (`runner.binder._to_workflow_input` or public `runner.binder.abind()`).
    - **Public Domain Seam Testing**: Tests asserting conversational memory hydration or rewind behavior must exercise the public domain contract (`await runner.binder.abind(req)` asserting `bound.hydrated_messages`), never piercing private internal hydration helpers (`_ahydrate_messages`).

11. **Self-Contained Domain Execution Events & Pure Transport Adapters (ADR-0022)**:
    - **Self-Contained Milestone Events**: `StepTransitionEvent` carries its own presentation attributes: canonical node name (`step: str`), human-readable progression label (`label: str`), and chronological milestone timestamp (`timestamp: float = datetime.now(UTC).timestamp()`). A Pydantic `@model_validator(mode="after")` enforces automatic fallback to `step` if `label` is omitted, empty, or whitespace-only, ensuring domain events are fully self-contained across any transport without downstream metadata hydration.
    - **Single Authority for Step Label Resolution**: `WorkflowRunner._stream_generator` is the sole authority responsible for resolving progression labels at transition emission time. It resolves labels from `WorkflowDefinition.info.step_labels` (defaulting to `DEFAULT_STEP_LABELS` or `node_name`) and yields fully hydrated `StepTransitionEvent(step=node_name, label=resolved_label)`.
    - **Pure Transport Adapters (Zero-Shim)**: Presentation adapters (such as `SseStreamAdapter` in `app/api/streaming.py`) must be pure stream transformers accepting only `events: AsyncIterable[WorkflowExecutionEvent]` without requiring external lookup dictionaries (`step_labels`), framework contexts, or timestamp synthesizers.
    - **Zero-Inspection Transport Routes**: Presentation routes (such as `POST /api/chat/stream` in `app/api/routes.py`) must remain minimal protocol adapters (< 15 lines). They delegate directly to `runner.stream(wf_input)` and pipe the resulting domain event stream straight into the transport adapter (`SseStreamAdapter.format(...)`), completely eliminating cross-seam workflow resolution, definition inspection, and label table fallbacks in the HTTP layer.
