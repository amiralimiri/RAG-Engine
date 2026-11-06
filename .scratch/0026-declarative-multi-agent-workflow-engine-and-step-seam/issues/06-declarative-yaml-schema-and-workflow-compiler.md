# 06: Declarative YAML Schema & WorkflowCompiler

**What to build:**
The declarative YAML workflow manifest schema and `WorkflowCompiler` in `app/rag/workflows/compiler.py`.
Defines Pydantic v2 models for workflow definitions (`StepConfig`, `EdgeConfig`, `WorkflowManifest`), capable of parsing complete workflow topologies from YAML strings or files.
Implements `WorkflowCompiler.compile(manifest: WorkflowManifest, ...) -> WorkflowDefinition` which:
1. Resolves and instantiates each declared step from `StepCatalog`.
2. Assembles a LangGraph `StateGraph(AgentState)` adding nodes with callable step instances.
3. Automatically synthesizes dynamic conditional edge routers for steps declaring `handoffs: [...]` based on `state["active_agent"]`.
4. Connects explicit edges and conditional routing edges.
5. Compiles and wraps the runnable graph in a `CompiledDeclarativeWorkflow` satisfying `WorkflowDefinition`.

**Blocked by:**
04: Multi-Agent Step with Dynamic Handoffs, 05: Zero-Shim Refactoring of Engine Factory

**Status:**
ready-for-agent

- [ ] Create `app/rag/workflows/compiler.py` with Pydantic v2 `StepConfig`, `EdgeConfig`, and `WorkflowManifest` models.
- [ ] Implement `WorkflowCompiler` class with `compile(manifest, ...)` and `compile_yaml(yaml_str_or_path, ...)`.
- [ ] Implement dependency resolvers for tools, chat models, and prompt store during step instantiation.
- [ ] Implement automatic edge synthesis for `handoffs: [...]` lists on agent steps.
- [ ] Implement `CompiledDeclarativeWorkflow` implementing the `WorkflowDefinition` protocol.
- [ ] Add unit test suite in `tests/rag/workflows/test_compiler.py` verifying YAML parsing, validation errors, step resolution, dynamic handoff edge construction, and execution.
- [ ] Pass `ruff check` and `pyright` on modified modules.
