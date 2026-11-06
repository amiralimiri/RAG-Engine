# ADR 0026: Declarative Multi-Agent Workflow Engine and Step Seam

## Status
Accepted (Extends ADR 0006, ADR 0007, ADR 0010, ADR 0014, ADR 0015, ADR 0025)

## Context
The platform has evolved through several milestones:
- **ADR-0006** unified the agentic RAG topology and established the `WorkflowClassifier` seam (`app/rag/classifiers.py`).
- **ADR-0007** introduced the `WorkflowDefinition` protocol and `WorkflowRegistry` for multi-workflow extensibility.
- **ADR-0010** extracted the execution pipeline into `WorkflowRunner`.
- **ADR-0014** and **ADR-0015** modernized ReAct agents and added pluggable workflow directory bundles (`load_from_directory`) with `manifest.json`.

However, as the system expands to support declarative multi-agent workflows, multimodal inputs, and rapid side-by-side benchmarking, significant architectural friction remains:
1. **Monolithic Factory Closures in `engine.py`**: `create_agentic_rag_graph` is a 250-line monolithic factory embedding 9 nested closures (`node_condense_query`, `node_retrieve_kb`, `node_grade_kb`, `rewrite_query`, `generate_from_kb`, `generate_from_web`, `direct_answer`, etc.). Each closure intermingles prompt compilation, LLM invocation, citation extraction, and tool unwrapping. None of these step policies can be unit-tested, configured, or reused across alternative workflows without executing the entire LangGraph.
2. **Lack of a Declarative (YAML) Authoring Workflow**: Defining new workflows currently requires writing custom Python classes adhering to `WorkflowDefinition` and manually wiring LangGraph topologies in code. Users cannot rapidly declare, swap, or benchmark alternative agent topologies using concise configuration manifests.
3. **Rigid State Representation**: `AgentState` in `app/rag/state.py` hardcodes single-turn, workflow-specific keys (`kb_docs: list[Document]`, `web_results: str`, `kb_grade: str`, `web_grade: str`, `retry_count: int`), creating namespace collisions in multi-agent environments where different agents query distinct tools or maintain distinct scratchpads.
4. **No First-Class Multi-Agent Handoffs or Multimodal Seams**: Transferring control between specialized agents requires hand-crafting custom router nodes, and non-text attachments (images, PDFs, documents) are currently relegated to untyped dictionaries (`attachments: list[dict[str, Any]]`).

### Core Design Principle
**"Fast & Simple by Default, Infinite Flexibility when Needed"**:
Creating workflows—especially multi-agent networks—must be frictionless with minimal YAML boilerplate for common patterns (agent handoffs, supervisor/workers, parallel execution, tool scoping). At the same time, the architecture must never box users in: 100% flexibility (escape hatches) must exist to handle arbitrary topologies, custom routing conditions, or bespoke step logic via standard Python extensions.

---

## Decisions

### 1. The `WorkflowStep` Protocol and Step Seam (`app/rag/steps/`)
Decompose the monolithic closures of `app/rag/engine.py` into cohesive, reusable step classes under a new dedicated package `app/rag/steps/`:
- **Protocol Contract**:
  ```python
  @runtime_checkable
  class WorkflowStep(Protocol):
      """Protocol seam defining execution contracts for discrete workflow nodes."""

      async def execute(
          self, state: dict[str, Any], config: RunnableConfig | None = None
      ) -> dict[str, Any]:
          ...

      async def __call__(
          self, state: dict[str, Any], config: RunnableConfig | None = None
      ) -> dict[str, Any]:
          """Enable direct plug-and-play addition to LangGraph (e.g. graph.add_node('name', step))."""
          ...
  ```
- **Discrete Step Modules**:
  - `app/rag/steps/base.py`: Declares `WorkflowStep`, base step class with default `__call__` forwarding to `execute`, context key mapping helpers (`inputs`/`outputs`), and the `StepCatalog`.
  - `app/rag/steps/retrieval.py`: `ToolRetrievalStep` wrapping `RAGTool` invocations, evidence aggregation, and artifact extraction.
  - `app/rag/steps/classifier.py`: `ClassifierGradingStep` and `ClassifierRouteStep` wrapping `WorkflowClassifier` seam calls.
  - `app/rag/steps/generation.py`: `PromptGenerationStep` (synthesizing answers from KB, web, or direct prompts via `PromptStore`) and `QueryReformulationStep` (handling query condensation and query rewriting).
  - `app/rag/steps/agent.py` (Phase 2): `AgentStep` encapsulating autonomous tool-calling loops with scoped tools and dynamic handoff synthesis.

### 2. `StepCatalog` and Custom Step Registration
Provide a thread-safe registry for discovering and instantiating steps:
- Built-in aliases: `tool_retrieval`, `classifier_grade`, `classifier_route`, `prompt_generate`, `query_reformulate`, `agent`.
- Registration decorator: `@register_step("alias")`.
- Escape hatch: If a step in a YAML definition specifies a dotted Python path (e.g., `type: "my_module.custom:CustomStepClass"`), the compiler dynamically resolves and instantiates the class.

### 3. Context Scoping & Message-Centric Blackboard State
Evolve graph state to support multi-agent collaboration and multimodal payloads:
- **Canonical `Attachment` Value Object**:
  ```python
  class Attachment(BaseModel):
      id: str
      mime_type: str
      url: str | None = None
      content_bytes: bytes | None = None
      metadata: dict[str, Any] = Field(default_factory=dict)
  ```
- **State Evolution**: Deprecate rigid single-workflow keys in favor of:
  - `messages: Sequence[BaseMessage]`: Canonical multi-turn conversational transcript.
  - `attachments: list[Attachment]`: Strongly typed multimodal inputs.
  - `context: dict[str, Any]`: Structured blackboard data store for inter-step communication.
  - `active_agent: str | None`: Multi-agent tracking.
  - `citations: Sequence[Citation]`: Canonical source attributions.
  - `trace: list[str]`: Reasoning trajectory.
- **Declarative Key Mapping**: Steps optionally declare `inputs` and `outputs` mappings in YAML to read/write specific paths in `context` (e.g. `inputs: { query: "context.condensed_query" }`, `outputs: { evidence: "context.security_kb" }`). When omitted, sensible zero-config defaults apply.

### 4. Hybrid Declarative YAML Schema & `WorkflowCompiler`
Introduce `WorkflowCompiler` (`app/rag/workflows/compiler.py`) to parse `.yaml` workflow manifests into `WorkflowDefinition` instances:
- **Concise Multi-Agent Handoffs**:
  ```yaml
  id: it_support_multi_agent
  name: IT Support Multi-Agent
  nodes:
    triage_agent:
      type: agent
      prompt: "rag:agent:triage"
      tools: [kb_tool]
      handoffs: [security_agent, billing_agent, finish]
    security_agent:
      type: agent
      prompt: "rag:agent:security"
      tools: [security_kb_tool]
      handoffs: [triage_agent, finish]
  ```
  The compiler automatically generates dynamic transfer tools (`transfer_to_security`, etc.) and the corresponding LangGraph conditional edges.
- **Explicit Routing Fallback**: Workflows can also define explicit conditional edges (`routing: condition -> target`) or reference custom Python router functions when non-linear logic is required.

### 5. Backward-Compatible Zero-Shim Integration for `engine.py`
To preserve existing tests, API endpoints, and workflows (`fix_single_jev`, `fix_single_llm`):
- `create_agentic_rag_graph` in `app/rag/engine.py` maintains its exact signature.
- Internally, `create_agentic_rag_graph` instantiates the newly extracted `WorkflowStep` classes from `app/rag/steps/` and adds them to `StateGraph`.
- The factory shrinks from ~250 lines of nested closures to ~35 lines of declarative graph wiring.

---

## Phased Rollout Plan

- **Phase 1 (Immediate Scope)**:
  1. Create `app/rag/steps/` package with `WorkflowStep` protocol, `StepCatalog`, and concrete step classes (`ToolRetrievalStep`, `ClassifierGradingStep`, `PromptGenerationStep`, `QueryReformulationStep`).
  2. Refactor `app/rag/engine.py` to wire these steps into `create_agentic_rag_graph` without breaking existing caller interfaces or tests.
  3. Introduce unit tests verifying each `WorkflowStep` in complete isolation.
- **Phase 2**:
  1. Introduce `WorkflowCompiler` and the YAML workflow schema.
  2. Implement `AgentStep` with dynamic `handoffs` tool synthesis.
  3. Register YAML-defined workflows automatically in `WorkflowRegistry`.
- **Phase 3**:
  1. Introduce the typed `Attachment` domain model in `app/rag/state.py` and API schemas.
  2. Migrate existing self-corrective RAG workflows to declarative YAML manifests.
  3. Enable side-by-side benchmarking across declared topologies.

---

## Consequences

### Positive
- **Locality**: Node execution logic (prompt formatting, tool execution, citation extraction) is cleanly decoupled from graph wiring.
- **Leverage**: Steps can be reused across arbitrary workflows; custom steps can be introduced via `@register_step` without modifying the core engine.
- **Test Surface**: Individual steps can be thoroughly tested via unit tests without spinning up a full LangGraph run.
- **Navigability**: `app/rag/engine.py` becomes a clean, readable topology blueprint.
- **Authoring Speed**: Multi-agent topologies can be written in concise YAML with automatic handoff routing.

### Negative / Trade-offs
- Adds an additional abstraction package (`app/rag/steps/`). Mitigated by having each step be directly callable (`__call__`) to keep LangGraph integration zero-overhead.
