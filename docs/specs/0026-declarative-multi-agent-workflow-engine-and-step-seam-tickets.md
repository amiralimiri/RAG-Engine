# Implementation Tickets: Declarative Multi-Agent Workflow Engine and Step Seam

- **ADR Reference:** `docs/adr/0026-declarative-multi-agent-workflow-engine-and-step-seam.md`
- **Technical Specification:** `docs/specs/0026-declarative-multi-agent-workflow-engine-and-step-seam-spec.md`
- **Ticket Directory:** `.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/`
- **Status:** Ready for Implementation
- **Triage Label:** `ready-for-agent`

---

## Dependency Graph

```text
[01: State & Multimodal Foundations]
                 │
                 ▼
[02: Core Step Seam & Catalog Infrastructure]
                 │
                 ▼
[03: Concrete Step Implementations]
       │                   │
       ▼                   ▼
[04: Multi-Agent Step]  [05: Zero-Shim Engine Refactor]
       │                   │
       └─────────┬─────────┘
                 ▼
[06: Declarative YAML Schema & WorkflowCompiler]
                 │
                 ▼
[07: WorkflowRegistry Discovery & Declarative Multi-Agent E2E]
```

---

## Ticket Breakdown

### [01: State & Multimodal Foundations](.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/01-state-and-multimodal-foundations.md)
- **Target Files:** `app/rag/state.py`, `tests/rag/test_state.py`
- **Blocked By:** None (can start immediately)
- **Delivers:**
  - `Attachment` immutable domain value object (`id`, `mime_type`, `url`, `content_bytes`, `metadata`).
  - Boundary validator `coerce_attachments` supporting strings, dicts, and model instances.
  - Evolved `AgentState(TypedDict)` supporting `messages`, `attachments`, `context: dict[str, Any]`, `active_agent: str | None`, `citations: Sequence[Citation]`, and `trace: list[str]`.
  - Full backward compatibility for legacy keys (`kb_docs`, `web_results`, `retry_count`, `kb_grade`, `web_grade`, etc.).
  - Unit tests verifying model validation, immutability, and boundary coercion.

---

### [02: Core Step Seam & Catalog Infrastructure](.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/02-core-step-seam-and-catalog-infrastructure.md)
- **Target Files:** `app/rag/steps/base.py`, `app/rag/steps/__init__.py`, `tests/rag/steps/test_step_base_and_catalog.py`
- **Blocked By:** `01: State & Multimodal Foundations`
- **Delivers:**
  - `WorkflowStep(Protocol)` with `async def execute(...)` and `async def __call__(...)` allowing direct addition to LangGraph nodes.
  - Abstract `BaseStep` with dot-notated input/output mapping (`resolve_input`, `apply_outputs`).
  - Thread-safe `StepCatalog`, `@register_step(alias)` decorator, and dynamic dotted-path Python resolver (`module.path:ClassName`).
  - Unit test suite verifying protocol compliance, dot-path resolution, and dynamic imports.

---

### [03: Concrete Step Implementations](.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/03-concrete-step-implementations.md)
- **Target Files:** `app/rag/steps/retrieval.py`, `app/rag/steps/classifier.py`, `app/rag/steps/generation.py`, `app/rag/steps/__init__.py`, `tests/rag/steps/`
- **Blocked By:** `02: Core Step Seam & Catalog Infrastructure`
- **Delivers:**
  - `ToolRetrievalStep` (`@register_step("tool_retrieval")`) wrapping `RAGTool`, executing queries, and aggregating evidence.
  - `ClassifierRouteStep` and `ClassifierGradingStep` (`@register_step("classifier_route")`, `@register_step("classifier_grade")`) wrapping `WorkflowClassifier`.
  - `PromptGenerationStep` (`@register_step("prompt_generate")`) compiling prompts from `PromptStore` and invoking chat models.
  - `QueryReformulationStep` (`@register_step("query_reformulate")`) handling condensation and query rewrites.
  - Unit test suites verifying each step in complete isolation with offline test doubles.

---

### [04: Multi-Agent Step with Dynamic Handoffs](.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/04-multi-agent-step-with-dynamic-handoffs.md)
- **Target Files:** `app/rag/steps/agent.py`, `app/rag/steps/__init__.py`, `tests/rag/steps/test_agent_step.py`
- **Blocked By:** `03: Concrete Step Implementations`
- **Delivers:**
  - `AgentStep` (`@register_step("agent")`) encapsulating autonomous tool-calling loops with scoped tools and prompts.
  - Dynamic handoff tool generator `create_handoff_tool(target_agent)` creating `transfer_to_<target>` tools.
  - Automatic state updates (`active_agent = target_agent`) upon transfer tool invocation.
  - Unit test suite verifying autonomous execution and dynamic handoff tool triggering.

---

### [05: Zero-Shim Refactoring of Engine Factory](.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/05-zero-shim-refactoring-of-engine-factory.md)
- **Target Files:** `app/rag/engine.py`, `tests/rag/test_engine.py`, `tests/rag/test_workflows.py`
- **Blocked By:** `03: Concrete Step Implementations`
- **Delivers:**
  - Deletion of the 9 nested closure functions inside `create_agentic_rag_graph`.
  - Clean ~35-line declarative topology wiring instantiated `WorkflowStep` classes into `StateGraph`.
  - 100% preservation of public signatures and exports with zero regression across all existing tests.

---

### [06: Declarative YAML Schema & WorkflowCompiler](.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/06-declarative-yaml-schema-and-workflow-compiler.md)
- **Target Files:** `app/rag/workflows/compiler.py`, `tests/rag/workflows/test_compiler.py`
- **Blocked By:** `04: Multi-Agent Step with Dynamic Handoffs`, `05: Zero-Shim Refactoring of Engine Factory`
- **Delivers:**
  - Pydantic v2 `WorkflowManifest` schema (`StepConfig`, `EdgeConfig`, `WorkflowManifest`).
  - `WorkflowCompiler` parsing YAML manifests into `StateGraph` topologies.
  - Automatic conditional edge synthesis for `handoffs: [...]` lists on agent steps.
  - `CompiledDeclarativeWorkflow` implementing `WorkflowDefinition`.
  - Compiler test suite verifying YAML compilation, edge wiring, and step resolution.

---

### [07: WorkflowRegistry Discovery & Declarative Multi-Agent E2E](.scratch/0026-declarative-multi-agent-workflow-engine-and-step-seam/issues/07-workflow-registry-discovery-and-declarative-multi-agent-e2e.md)
- **Target Files:** `app/rag/workflows/registry.py`, `tests/fixtures/workflows/it_support_multi_agent.yaml`, `tests/rag/workflows/test_declarative_workflows_e2e.py`
- **Blocked By:** `06: Declarative YAML Schema & WorkflowCompiler`
- **Delivers:**
  - Updating `WorkflowRegistry.load_from_directory()` to scan and register `.yaml` workflow manifests.
  - Canonical declarative multi-agent IT support scenario manifest with Triage and Security agents.
  - End-to-end integration test suite verifying dynamic discovery, autonomous handoffs, and execution via `WorkflowRunner`.
  - Full suite verification passing `pytest`, `ruff`, and `pyright`.
