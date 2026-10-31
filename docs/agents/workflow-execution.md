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
   - The public execution seam (`execute`, `aexecute`, `stream`) returns standardized domain models (`ChatResponse` or SSE string stream).
   - For offline evaluation harnesses, benchmark experiments, or diagnostic suites requiring normalized retrieval context and evidence, use `runner.execute_with_context(request) -> ExecutionResult` (per ADR-0013). This bundles the generated response with `EvalContext` without exposing raw internal graph state or piercing private methods (`_execute_with_state`).
   - Public transport routes strictly consume `execute()` or `stream()`.

4. **Preserve Child Tool Callbacks**:
   - Never strip callbacks (`child_config["callbacks"] = []`) inside tool wrappers to suppress duplicate firing.
   - Address seam differences at the tool dispatch boundary (e.g. invoking directly patched `BaseTool` instances cleanly in engine nodes) so Langfuse and OpenTelemetry callback spans propagate uninterrupted.

5. **Centralized Workflow Resolution**:
   - Always resolve workflows through `WorkflowRunner._resolve_workflow()` so that auto-registration of canonical default workflows (`fix_single_llm`, `fix_single_jev`, `react_agent`) is identical across all entrypoints (`execute`, `aexecute`, `stream`). Never perform raw registry dictionary lookups that bypass default registration.

6. **Streaming Error Fallback Preservation**:
   - When an unhandled exception occurs in `WorkflowRunner.stream()`, invoke `workflow_def.handle_error(exc, state)` and yield fallback tokens and metadata frames before terminating with the final SSE error frame.

7. **Autonomous ReAct Agent & `create_agent` Seam Standards (ADR-0014)**:
   - Modernize agent workflows to compile via `from langchain.agents import create_agent` rather than deprecated `langgraph.prebuilt.create_react_agent`.
   - Pass instructions to `create_agent` using `system_prompt=...` (not `prompt=...`).
   - Node identifiers in `create_agent` default to `"model"` for reasoning and `"tools"` for execution.
   - Dual-map generation nodes (`DEFAULT_REACT_GENERATION_NODES = {"agent", "model"}`) and step labels (`DEFAULT_REACT_STEP_LABELS`) in workflow metadata to preserve SSE token streaming and step updates across both modern topologies and mock test fixtures.
   - Resilient query fallback: When converting graph states to `ChatResponse`, fall back to the latest `HumanMessage` in conversation history if `final_state.get("question")` is omitted by the agent state schema.
   - Harmonized evidence attribution: Evidence source resolution (`source_used`) must strictly align across `to_chat_response` and `extract_eval_context` using a dedicated helper (`_resolve_source_used(tools_called=...)`).
   - Tool message identification: Consolidate duck-typed message checking using a private helper `_is_tool_message(m)` (`isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool"`).
