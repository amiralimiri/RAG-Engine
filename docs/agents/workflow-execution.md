# Workflow Execution & Pipeline Seam Patterns

Guidelines and lessons learned from ADR-0010 (Workflow Runner Execution Pipeline & Engine Deepening) for orchestrating LangGraph workflows, token streaming, telemetry context binding, and online evaluation.

---

## 1. Core Principles

1. **Direction of Dependencies (No Reverse Coupling)**:
   - Domain modules (`app/rag/**`) must **never** import from, inspect, or couple to the transport/API layer (`app/api/**`).
   - If tests or transport handlers need custom telemetry, callback hooks, or background evaluation tasks, inject them via constructor parameters or method arguments on `WorkflowRunner` (e.g. `callback_factory`, `trace_extractor`, `telemetry_flusher`, `eval_task_fn`), falling back to core domain singletons.

2. **PEP 484 Standards Compliance**:
   - Always type-hint optional parameters with explicit union syntax: `config: RunnableConfig | None = None`.
   - Never write `config: RunnableConfig = None` (prohibited under PEP 484).

3. **No Dynamic Pydantic Model Mutation**:
   - Never dynamically attach private attributes to Pydantic models (e.g. `chat_response._raw_state = final_state`).
   - If callers require internal execution state (such as legacy shims or evaluation bridges), provide explicit typed dual-return methods:
     ```python
     response, final_state = runner.execute_with_state(request)
     ```

4. **Preserve Child Tool Callbacks**:
   - Never strip callbacks (`child_config["callbacks"] = []`) inside tool wrappers to suppress duplicate firing.
   - Address seam differences at the tool dispatch boundary (e.g. invoking directly patched `BaseTool` instances cleanly in engine nodes) so Langfuse and OpenTelemetry callback spans propagate uninterrupted.

5. **Centralized Workflow Resolution**:
   - Always resolve workflows through `WorkflowRunner._resolve_workflow()` so that auto-registration of canonical default workflows (`fix_single_llm`, `fix_single_jev`, `react_agent`) is identical across all entrypoints (`execute`, `aexecute`, `stream`). Never perform raw registry dictionary lookups that bypass default registration.

6. **Streaming Error Fallback Preservation**:
   - When an unhandled exception occurs in `WorkflowRunner.stream()`, invoke `workflow_def.handle_error(exc, state)` and yield fallback tokens and metadata frames before terminating with the final SSE error frame.
