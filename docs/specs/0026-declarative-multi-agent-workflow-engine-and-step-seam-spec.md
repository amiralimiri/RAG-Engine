# Technical Specification: Declarative Multi-Agent Workflow Engine and Step Seam

- **ADR Reference:** `docs/adr/0026-declarative-multi-agent-workflow-engine-and-step-seam.md` (extends ADR-0006, ADR-0007, ADR-0010, ADR-0014, ADR-0015, ADR-0025)
- **Target Subsystems:** `app/rag/state.py`, `app/rag/steps/`, `app/rag/engine.py`, `app/rag/workflows/`, `tests/`
- **Specification Document:** `docs/specs/0026-declarative-multi-agent-workflow-engine-and-step-seam-spec.md`
- **Status:** Approved for Implementation (Ready for Tickets)
- **Triage Label:** `ready-for-agent`

---

## Problem Statement

The RAG Engine has established clean protocol seams for classifiers, tools, memory, prompts, and evaluation. However, the workflow authoring and execution subsystem suffers from four compounding architectural bottlenecks:

1. **Monolithic Factory Closures in `engine.py`**:
   `create_agentic_rag_graph` in `app/rag/engine.py` is a 250-line monolithic function containing 9 nested closures (`node_condense_query`, `node_route_question`, `node_retrieve_kb`, `node_grade_kb`, `node_search_web`, `node_grade_web`, `rewrite_query`, `generate_from_kb`, `generate_from_web`, `node_direct_answer`). These closures tightly couple prompt rendering, tool result unpacking, citation extraction, and LangGraph topology wiring. None of these step policies can be unit-tested in isolation, reconfigured, or reused across different workflows.

2. **Inability to Author Workflows Declaratively (YAML)**:
   Authoring a new workflow topology currently requires creating new Python classes implementing `WorkflowDefinition` and manually constructing a LangGraph `StateGraph` in Python code. Developers cannot define, modify, or swap workflow topologies using declarative manifests (such as YAML), preventing fast experimentation.

3. **Rigid, Workflow-Specific State Schema**:
   `AgentState` in `app/rag/state.py` hardcodes single-turn, self-corrective RAG fields (`kb_docs: list[Document]`, `web_results: str`, `kb_grade: str`, `web_grade: str`, `retry_count: int`). In a multi-agent topology—where specialized agents query different knowledge stores, inspect security logs, or maintain local working memory—these hardcoded keys create namespace collisions and prevent agent isolation.

4. **Absence of First-Class Multi-Agent Handoffs and Typed Multimodal Seams**:
   Passing execution between specialized agents requires handcrafting custom routing functions and state variables. Non-text inputs (images, logs, documents) are represented as untyped dictionaries (`attachments: list[dict[str, Any]]`), preventing structured multimodal processing and violating our Zero-Shim architectural standards.

---

## Solution

Build a declarative, modular multi-agent workflow platform guided by the core design principle: **"Fast & Simple by Default, Infinite Flexibility when Needed"**.

The solution decomposes the system across four foundational layers:

1. **Foundational State & Multimodal Models (`app/rag/state.py`)**:
   Introduce a canonical `Attachment` domain value object and evolve `AgentState` into an extensible, message-first blackboard state supporting `messages`, `attachments`, `context: dict[str, Any]`, `active_agent: str | None`, `citations: Sequence[Citation]`, and `trace: list[str]`, while maintaining 100% backward compatibility for existing callers of legacy keys.

2. **Step Seam and Catalog (`app/rag/steps/`)**:
   Define the `WorkflowStep` protocol seam and a centralized `StepCatalog` with `@register_step` and dynamic dotted-path Python resolution. Extract the closures of `app/rag/engine.py` into dedicated, callable step classes:
   - `ToolRetrievalStep` (`@register_step("tool_retrieval")`)
   - `ClassifierGradingStep` (`@register_step("classifier_grade")`) and `ClassifierRouteStep` (`@register_step("classifier_route")`)
   - `PromptGenerationStep` (`@register_step("prompt_generate")`) and `QueryReformulationStep` (`@register_step("query_reformulate")`)
   - `AgentStep` (`@register_step("agent")`) with automatic handoff tool generation.

3. **Zero-Shim Refactoring of `app/rag/engine.py`**:
   Refactor `create_agentic_rag_graph` into a clean, ~35-line declarative topology factory that instantiates and wires the new step classes into a `StateGraph`, maintaining an identical external interface and zero breaking changes for existing workflows and tests.

4. **Declarative YAML Workflow Schema & `WorkflowCompiler` (`app/rag/workflows/compiler.py`)**:
   Create a Pydantic v2 declarative schema and a `WorkflowCompiler` capable of reading `.yaml` workflow manifests, instantiating steps from `StepCatalog`, automatically compiling `handoffs: [...]` into transfer tools and conditional edges, and wrapping the compiled graph into a registered `WorkflowDefinition` for side-by-side benchmarking.

---

## User Stories

1. As a workflow author, I want to declare a complete workflow in a clean `.yaml` manifest, so that I can configure topologies, prompt keys, tool bindings, and routing without writing Python boilerplate.
2. As a multi-agent system designer, I want an `AgentStep` to declare `handoffs: [billing_agent, triage_agent]`, so that the engine automatically equips the agent with transfer tools and wires the corresponding conditional routing edges in LangGraph.
3. As a developer writing custom business logic, I want to reference bespoke Python steps via `type: "my_package.module:CustomStep"` or register them with `@register_step`, so that I have complete escape-hatch flexibility when built-in steps are insufficient.
4. As a step author, I want each step to declare `inputs` and `outputs` mappings against a shared `context` blackboard, so that I can control data flow and isolate step scratchpads with zero key collisions.
5. As an API client sending screenshots or error logs, I want attachments represented as strongly typed `Attachment` domain models with validated MIME types and IDs, so that downstream nodes handle multimodal payloads safely.
6. As an engineer writing unit tests, I want to instantiate and test `ToolRetrievalStep`, `ClassifierGradingStep`, and `PromptGenerationStep` in complete isolation without compiling a full LangGraph runnable.
7. As a maintainer of the existing `fix_single_llm` and `fix_single_jev` workflows, I want `create_agentic_rag_graph` in `app/rag/engine.py` to preserve its exact signature and return identical results, so that existing production workflows and test suites pass without regression.
8. As an evaluator running matrix benchmarks, I want to load and execute multiple YAML workflow variants side by side via `WorkflowRegistry`, so that I can compare prompt templates, model temperatures, and agent topologies on identical test datasets.
9. As a developer inspecting a running multi-agent workflow, I want `active_agent` and step execution milestones recorded in `state["trace"]`, so that telemetry and observability logs clearly reveal which agent handled each stage of inquiry resolution.
10. As a platform maintainer, I want all step instances to be directly callable (`step(state, config)`), so that LangGraph's `graph.add_node("name", step)` accepts them natively without wrapping closures or adapter shims.

---

## Implementation Decisions

### 1. State & Multimodal Foundations (`app/rag/state.py`)

- **Domain Model `Attachment`**:
  An immutable domain model representing multimodal and file payloads:
  ```python
  class Attachment(BaseModel):
      model_config = ConfigDict(frozen=True)

      id: str = Field(description="Unique identifier for the attachment")
      mime_type: str = Field(description="IANA media type (e.g. image/png, application/pdf)")
      url: str | None = Field(default=None, description="External or object storage URL")
      content_bytes: bytes | None = Field(default=None, repr=False, description="Raw content bytes if staged")
      metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary file metadata")
  ```
- **Boundary Validator `coerce_attachments`**:
  A boundary validator transforming raw dictionaries or string filenames into validated `Attachment` instances (matching the pattern of `coerce_citations`).
- **Generalized Blackboard `AgentState`**:
  Extend `AgentState` while preserving legacy keys for 100% backward compatibility:
  ```python
  class AgentState(TypedDict, total=False):
      # Primary message-centric blackboard
      question: str
      current_query: str
      messages: Annotated[list[BaseMessage], operator.add]
      attachments: list[Attachment]
      context: dict[str, Any]
      active_agent: str | None
      citations: Sequence[Citation]
      trace: Annotated[list[str], operator.add]
      trace_id: NotRequired[str]
      retrieved_contexts: NotRequired[list[str]]
      answer: str
      source_used: str

      # Backward-compatibility fields (preserved for legacy self-corrective tests/nodes)
      kb_docs: list[Document]
      web_results: str
      kb_grade: str
      web_grade: str
      retry_count: int
  ```

### 2. Step Seam and Catalog (`app/rag/steps/`)

Create a modular package `app/rag/steps/` containing:

- **`app/rag/steps/base.py`**:
  - `WorkflowStep(Protocol)`: Defines `async def execute(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]` and `async def __call__(...)`.
  - `BaseStep`: Abstract base class implementing `__call__` by delegating to `execute`, and providing declarative input/output extraction helpers:
    - `resolve_input(state, key, default=None)`: Resolves dot-notated paths (e.g., `context.query` or top-level `question`).
    - `apply_outputs(state_updates, outputs_map)`: Maps step return values into target blackboard keys (e.g. writing to `context.kb_evidence` or top-level `citations`).
  - `StepCatalog`: Thread-safe registry holding step factories and type mappings.
  - `@register_step(alias: str)`: Decorator registering a step class.
  - `resolve_step_type(identifier: str) -> type[WorkflowStep]`: Looks up registered alias or resolves dotted Python import paths (`module.path:ClassName`).

- **`app/rag/steps/retrieval.py` (`ToolRetrievalStep`)**:
  - Wraps execution of a `RAGTool` (such as KB retriever or Web search).
  - Accepts injected tool and inputs mapping (e.g., `query_key="current_query"`).
  - Invokes `tool.aexecute(query=query, config=config)`.
  - Normalizes evidence via `aggregate_tool_evidence`.
  - Emits state updates containing `retrieved_contexts`, `citations`, `source_used`, and raw artifacts in `context`.

- **`app/rag/steps/classifier.py` (`ClassifierGradingStep` & `ClassifierRouteStep`)**:
  - `ClassifierRouteStep`: Injects `WorkflowClassifier` and calls `classifier.route(question)`. Emits `source_used` (`"kb"` or `"direct"`).
  - `ClassifierGradingStep`: Injects `WorkflowClassifier`, evaluation target (`"kb"` or `"web"`). Calls `classifier.grade_kb(...)` or `classifier.grade_web(...)`. Emits `kb_grade` or `web_grade` (`"good"` or `"weak"`).

- **`app/rag/steps/generation.py` (`PromptGenerationStep` & `QueryReformulationStep`)**:
  - `PromptGenerationStep`: Injects `prompt_key: str`, `model: BaseChatModel`, and `prompt_store: PromptStore`. Formats chat history, compiles prompt, invokes model, and updates `answer`, `source_used`, and `trace`.
  - `QueryReformulationStep`: Injects `mode: Literal["condense", "rewrite"]`, `model`, and `prompt_store`. Condenses history or rewrites query based on retrieval feedback, updating `current_query` and incrementing `retry_count`.

- **`app/rag/steps/agent.py` (`AgentStep`)**:
  - Encapsulates an autonomous tool-calling loop using `create_react_agent` or native tool bindings.
  - Accepts scoped `tools: list[RAGTool]`, `prompt_key: str`, `model: BaseChatModel`, and optional `handoffs: list[str]`.
  - When `handoffs` are specified, dynamically creates transfer tools:
    ```python
    def create_handoff_tool(target_agent: str) -> StructuredTool: ...
    ```
    Invoking `transfer_to_<target>(reason: str)` appends an `AIMessage` with tool call and returns a transfer command, updating `active_agent = target_agent`.

### 3. Zero-Shim Refactoring of `app/rag/engine.py`

- Delete the 9 nested closure functions inside `create_agentic_rag_graph`:
  - `node_condense_query` -> instantiated `QueryReformulationStep(mode="condense", ...)`
  - `node_route_question` -> instantiated `ClassifierRouteStep(...)`
  - `node_retrieve_kb` -> instantiated `ToolRetrievalStep(tool=kb_tool, source_used="vectorstore", ...)`
  - `node_grade_kb` -> instantiated `ClassifierGradingStep(target="kb", ...)`
  - `node_search_web` -> instantiated `ToolRetrievalStep(tool=web_tool, source_used="web_search", ...)`
  - `node_grade_web` -> instantiated `ClassifierGradingStep(target="web", ...)`
  - `rewrite_query` -> instantiated `QueryReformulationStep(mode="rewrite", ...)`
  - `generate_from_kb` -> instantiated `PromptGenerationStep(prompt_key="rag:response", ...)`
  - `generate_from_web` -> instantiated `PromptGenerationStep(prompt_key="rag:web_response", ...)`
  - `node_direct_answer` -> instantiated `PromptGenerationStep(prompt_key="rag:direct_answer", ...)`
- `create_agentic_rag_graph` becomes a clean ~35-line declarative topology wiring nodes directly:
  ```python
  graph.add_node("condense_query", step_condense)
  graph.add_node("route_question", step_route)
  graph.add_node("retrieve_kb", step_retrieve_kb)
  # ...
  ```
- Public signature and return type (`CompiledStateGraph`) are strictly preserved.

### 4. Declarative Manifest Schema & `WorkflowCompiler` (`app/rag/workflows/compiler.py`)

- **Manifest Schema (`WorkflowManifest`)**:
  ```python
  class StepConfig(BaseModel):
      type: str = Field(description="Registered step alias or dotted path 'pkg.module:Class'")
      prompt: str | None = None
      tools: list[str] = Field(default_factory=list)
      model: str | None = None
      temperature: float | None = None
      inputs: dict[str, str] = Field(default_factory=dict)
      outputs: dict[str, str] = Field(default_factory=dict)
      handoffs: list[str] = Field(default_factory=list)
      parameters: dict[str, Any] = Field(default_factory=dict)

  class EdgeConfig(BaseModel):
      from_node: str
      to_node: str | None = None
      condition: str | None = None  # Built-in condition name or Python dotted path
      targets: dict[str, str] = Field(default_factory=dict)

  class WorkflowManifest(BaseModel):
      id: str
      name: str
      description: str = ""
      is_conversational: bool = False
      supports_streaming: bool = True
      entrypoint_node: str = "START"
      generation_nodes: list[str] = Field(default_factory=list)
      step_labels: dict[str, str] = Field(default_factory=dict)
      nodes: dict[str, StepConfig]
      edges: list[EdgeConfig] = Field(default_factory=list)
  ```
- **Workflow Compilation**:
  - `WorkflowCompiler.compile(manifest: WorkflowManifest, tool_resolver, model_resolver, prompt_store) -> WorkflowDefinition`:
    1. Instantiates each `WorkflowStep` from `manifest.nodes` via `StepCatalog`.
    2. Builds a `StateGraph(AgentState)`.
    3. Adds nodes using callable step instances.
    4. For steps declaring `handoffs: [agent_b, finish]`, synthesizes dynamic conditional edge routing on `state["active_agent"]`.
    5. Adds declared explicit edges and conditional edges.
    6. Compiles graph and wraps in a `CompiledDeclarativeWorkflow` implementing `WorkflowDefinition`.
- **Integration with `WorkflowRegistry`**:
  Update `WorkflowRegistry.load_from_directory()` to scan for both `manifest.json` and `workflow.yaml` files, registering declarative workflows automatically.

---

## Testing Decisions

### Standards for Good Tests
- **Test External Behavior, Not Implementation Details**: Tests verify observable state transitions across the `WorkflowStep` interface (`execute(state, config) -> state_updates`) and compiled graph executions (`ainvoke`), never asserting on private closure internals.
- **Offline Determinism**: All step and compiler tests run completely offline using `FakeChatModel`, `FakePromptStore`, `FakeRAGTool`, and `FakeWorkflowClassifier`.

### Test Suites to Implement

1. **Step Seam Unit Tests (`tests/rag/steps/`)**:
   - `test_step_base_and_catalog.py`: Verifies `@register_step`, `StepCatalog` lookup, dotted-path resolution, and `inputs`/`outputs` context mapping.
   - `test_tool_retrieval_step.py`: Verifies `ToolRetrievalStep` invoking `FakeRAGTool`, aggregating evidence, and setting citations and `retrieved_contexts`.
   - `test_classifier_steps.py`: Verifies `ClassifierRouteStep` and `ClassifierGradingStep` invoking classifier seams and setting state variables.
   - `test_generation_steps.py`: Verifies `PromptGenerationStep` compiling prompts and invoking models, and `QueryReformulationStep` performing condensation and query rewriting.
   - `test_agent_step_and_handoffs.py`: Verifies `AgentStep` executing tool loops and firing dynamic handoff tools to update `active_agent`.

2. **Engine Refactoring Regression Tests (`tests/rag/test_engine_steps.py` & existing suites)**:
   - Run all existing engine tests (`tests/rag/test_engine.py`, `tests/rag/test_workflows.py`, `tests/rag/test_react_agent.py`) without modifying test assertions, ensuring zero regression.
   - Verify that `create_agentic_rag_graph` contains clean modular step instances and no nested closures.

3. **Compiler & Manifest Integration Tests (`tests/rag/workflows/test_compiler.py`)**:
   - Parse sample `workflow.yaml` strings (linear RAG, supervisor/workers, multi-agent handoffs).
   - Verify compilation into `WorkflowDefinition` with correct `generation_nodes` and `step_labels`.
   - Execute compiled declarative workflows end-to-end with mock tools/models and assert correct turn outcomes.
   - Verify invalid YAML manifests (missing nodes, unresolvable steps, circular handoffs) fail fast with descriptive validation errors.

---

## Out of Scope

- **Modifying wire transport schemas**: `ChatRequest` and `ChatResponse` HTTP schemas remain wire-compatible.
- **Frontend changes**: No changes to frontend HTML/JS/CSS templates.
- **Database schema migrations**: SQLite conversation memory persistence retains its existing schema; attachments in memory continue to serialize via existing JSON attachments columns.
- **External model vendor changes**: No new LLM provider integrations are added in this milestone.

---

## Further Notes

- **Phase-Gating Invariant**: Modifying application code in `app/` is strictly forbidden until this specification is approved and broken into execution tickets via `/to-tickets`.
- **Zero-Shim Rule**: Steps must never add dict-like fallback methods (`__getitem__`, `__setitem__`) to domain models. State transformations must use validated Pydantic models or clean typed dictionaries.
- **Forward Compatibility**: All step classes are designed to operate both within the existing `AgentState` environment and the future declarative blackboard compiler environment without refactoring.
