# 04: `ExecutionProfile` 3-Tier Resolution & Multi-Dimensional Telemetry

**What to build:**
Implement parameterized profiles and rich multi-dimensional telemetry tracking:
1. Define `ExecutionProfile` in `app/rag/workflows/base.py` (`variant_id`, `model_name`, `prompt_version`, `temperature`, `parameters`, `tags`, `metadata`).
2. Add profile registry methods to `WorkflowRegistry`:
   - `register_profile(workflow_id: str, profile: ExecutionProfile) -> None`
   - `get_profile(workflow_id: str, variant_id: str) -> ExecutionProfile | None`
   - `list_profiles(workflow_id: str) -> list[ExecutionProfile]`
3. Add optional `variant_id: str | None = None` and `parameters: dict[str, Any] | None = None` to `ChatRequest` (in `app/api/schemas.py`) and map to `WorkflowInput`.
4. Update `WorkflowRunner._prepare_execution(...)`:
   - Implement 3-tier precedence hierarchy: Explicit Overrides > Registered Profile > Workflow Baseline.
   - Inject resolved parameters into LangGraph's `RunnableConfig["configurable"]`.
   - Propagate multi-dimensional tags to Langfuse: `workflow:<id>`, `variant:<id>`, `model:<name>`, `prompt:<ver>`, `env:<env>` and structured metadata.
5. In `PluggableWorkflow`, support optional variant-cached graph compilation via `get_compiled_graph(variant_id: str | None = None)` (Approach B fallback).
6. Add unit tests in `tests/rag/test_workflow_runner.py` verifying 3-tier resolution, configurable dictionary injection, and Langfuse callback handler tags.

**Blocked by:** 01: Domain DTOs (`WorkflowInput`, `WorkflowOutput`) & Transport Decoupling

**Status:** closed

- [x] `ExecutionProfile` model defined in `app/rag/workflows/base.py`
- [x] Profile management added to `WorkflowRegistry`
- [x] `ChatRequest` and `WorkflowInput` accept optional `variant_id` and `parameters`
- [x] 3-tier precedence resolution implemented in `WorkflowRunner._prepare_execution`
- [x] `RunnableConfig["configurable"]` injected with resolved parameters
- [x] Langfuse callback handler tagged with `variant:<id>`, `model:<name>`, `prompt:<ver>`
- [x] Cached per-variant compilation hook supported in `PluggableWorkflow`
- [x] Unit tests in `tests/rag/test_workflow_runner.py` pass: `uv run pytest tests/rag/test_workflow_runner.py`
