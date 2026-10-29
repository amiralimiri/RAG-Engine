# Technical Specification: Multi-Workflow Extensibility Platform

- **ADR Reference:** `docs/adr/0007-multi-workflow-extensibility-platform.md`
- **Target Version:** `0.7.0`
- **Status:** In Progress

---

## 1. Problem Statement

Following ADR 0006, the self-corrective RAG engine consolidated its 10-node topology and established the `WorkflowClassifier` seam (`app/rag/classifiers.py`), allowing both `fix_single_llm` and `fix_single_jev` to share graph definitions.

However, the architecture remains rigidly coupled to a single fixed-graph topology:
1. **Module-Level Routing Coupling**: `app/api/routes.py` hardcodes module-level dictionaries (`WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`), generation node names (`GENERATION_NODES`), and step labels (`STEP_LABELS`).
2. **Monolithic State Schema**: The execution runtime explicitly assumes all workflows run on `AgentState` (`kb_docs`, `web_grade`, `retry_count`, `citations`), preventing developers from introducing conversational ReAct agents, multi-turn chat agents with memory, or multimodal pipelines.
3. **Hardwired Tool Invocations**: Knowledge base retrieval (`retrieve_kb`) and search (`search_web`) are bespoke graph functions in `engine.py`, unavailable as structured `BaseTool` instances for autonomous tool-calling agents.
4. **Single-Turn API Contract**: `ChatRequest` only accepts a single `question: str`, omitting conversational history (`messages`) or attachments.
5. **Streaming Leakage**: SSE token streaming assumes every workflow emits text directly from three fixed generation nodes, leaking raw JSON tool-calling deltas to the frontend when autonomous agents invoke tools.

To support rapid development of new agentic workflows, the platform must provide an open-closed architecture where workflows encapsulate their own state schemas, graph compilation, tool bindings, streaming filters, and evaluation extractors.

---

## 2. Architectural Solution

Establish an extensible multi-workflow architecture comprised of five foundational modules:

1. **`app/rag/workflows/base.py` (`WorkflowDefinition` & `WorkflowInfo`)**:
   - A runtime-checkable `Protocol` defining the workflow lifecycle contract: metadata, graph retrieval, bidirectional state conversion, evaluation context extraction, and error fallback handling.
2. **`app/rag/workflows/registry.py` (`WorkflowRegistry`)**:
   - A thread-safe registry providing lazy graph compilation, singleton caching, workflow discovery, and optional startup pre-warming.
3. **`app/rag/tools/` (Modular Tool Abstraction Layer)**:
   - Standalone LangChain `BaseTool` factories with structured Pydantic `args_schema` (`KnowledgeSearchInput`, `WebSearchInput`) and automatic Langfuse telemetry propagation.
4. **`app/rag/workflows/self_corrective.py` (`SelfCorrectiveRagWorkflow`)**:
   - A single parameterized workflow class hosting both `fix_single_llm` and `fix_single_jev` via their respective ADR 0006 classifier adapters.
5. **`app/rag/workflows/react_agent.py` (`ReActAgentWorkflow`)**:
   - A reference autonomous tool-calling agent using `langgraph.prebuilt.create_react_agent` with in-memory checkpointer persistence (`MemorySaver`) via `session_id` -> `thread_id` mapping.
6. **Transport Layer Modernization (`app/api/routes.py` & `app/api/schemas.py`)**:
   - Additive `ChatRequest` fields (`messages`, `attachments`), enriched `GET /api/workflows` capability introspection, and tool-call token filtering in the SSE stream generator.
7. **Conformance Test Harness (`tests/rag/test_workflow_contracts.py`)**:
   - A reusable `BaseWorkflowContractTest` class guaranteeing compliance with state adaptation, telemetry, and error-handling standards.

---

## 3. User Stories

1. **As a backend developer**, I want to create a new workflow by subclassing or implementing `WorkflowDefinition`, so that I can add new agent topologies without modifying FastAPI transport routes or existing workflows.
2. **As an agent developer**, I want to use standard LangChain tools (`retriever_tool`, `web_search_tool`) inside dynamic ReAct agents, so that I don't have to reimplement search or telemetry logic.
3. **As a frontend developer**, I want `GET /api/workflows` to return rich capabilities metadata (name, description, conversational flag, step labels), so that the UI can dynamically configure its chat interface and step spinners.
4. **As an enterprise user**, I want multi-turn conversations with the ReAct agent to remember previous context within the same `session_id`, without needing to resend the entire message history every turn.
5. **As an API consumer**, I want existing requests to `POST /api/chat` and `POST /api/chat/stream` with `workflow="fix_single_llm"` and `workflow="fix_single_jev"` to function with zero breaking changes.
6. **As a streaming client**, I want tool-calling reasoning tokens (JSON function arguments) filtered out of the SSE `token` stream, so that only user-facing conversational text is rendered in the chat bubble.
7. **As an LLMOps engineer**, I want online evaluation (`run_online_evaluation_task`) to evaluate any workflow through a standardized `extract_eval_context()` hook, ensuring metrics are recorded without key errors.
8. **As a QA engineer**, I want to run `BaseWorkflowContractTest` against any new workflow, ensuring that all state adapters, fallbacks, and schema guarantees are validated automatically in CI.

---

## 4. Detailed Component Design

### 4.1. Core Protocol & Schemas (`app/rag/workflows/base.py`)

```python
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel, Field
from app.api.schemas import ChatRequest, ChatResponse

class WorkflowInfo(BaseModel):
    id: str
    name: str
    description: str
    is_conversational: bool = False
    supports_streaming: bool = True
    step_labels: dict[str, str] = Field(default_factory=dict)
    generation_nodes: set[str] = Field(default_factory=set)

class EvalContext(BaseModel):
    source_used: str = "direct"
    retrieved_contexts: list[str] = Field(default_factory=list)

@runtime_checkable
class WorkflowDefinition(Protocol):
    info: WorkflowInfo

    def get_compiled_graph(self) -> Any:
        """Return the compiled, thread-safe LangGraph runnable."""
        ...

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        """Convert an incoming API ChatRequest into internal workflow graph state."""
        ...

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        """Convert the internal final graph state into a normalized ChatResponse."""
        ...

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        """Extract evidence contexts and source used for online evaluation."""
        ...

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        """Provide a fallback final state dictionary in the event of an unhandled node exception."""
        ...
```

### 4.2. Workflow Registry (`app/rag/workflows/registry.py`)

- Maintain an internal registry map `_REGISTRY: dict[str, WorkflowDefinition]`.
- Provide thread-safe methods:
  - `register(workflow: WorkflowDefinition) -> None`
  - `get(workflow_id: str) -> WorkflowDefinition`
  - `list_info() -> list[WorkflowInfo]`
  - `list_ids() -> list[str]`
  - `warmup() -> None` (compiles all registered graphs)
- Decorator `@register_workflow` for declarative registration.

### 4.3. Tool Abstraction Layer (`app/rag/tools/`)

- `app/rag/tools/retriever.py`:
  - `KnowledgeSearchInput`: Pydantic schema with `query: str`.
  - `create_retriever_tool(retriever=None) -> BaseTool`: Encapsulates Pinecone vectorstore lookup and returns formatted chunk text.
- `app/rag/tools/search.py`:
  - `WebSearchInput`: Pydantic schema with `query: str`.
  - `create_web_search_tool() -> BaseTool`: Encapsulates Tavily general search.
- All tools pass `RunnableConfig` through, ensuring Langfuse telemetry callbacks log tool spans.

### 4.4. Parameterized Self-Corrective RAG (`app/rag/workflows/self_corrective.py`)

- Subclasses/implements `WorkflowDefinition`.
- Parameterized by `classifier: WorkflowClassifier`.
- Compiles the canonical 10-node DAG from `app/rag/engine.py`.
- Encapsulates `AgentState` conversion, citations extraction, and error handling.
- Registered twice:
  - `id="fix_single_llm"` with `OpenAIClassifierAdapter()`
  - `id="fix_single_jev"` with `JevClassifierAdapter()`

### 4.5. Reference ReAct Agent (`app/rag/workflows/react_agent.py`)

- Subclasses/implements `WorkflowDefinition`.
- Uses `langgraph.prebuilt.create_react_agent(llm, tools, checkpointer=MemorySaver())`.
- State is a list of LangChain messages (`Sequence[BaseMessage]`).
- Converts `ChatRequest.question` and optional `messages` into `HumanMessage`.
- Maps `session_id` into `thread_id` for conversation persistence across invocations.
- Extracts citations from tool messages (`ToolMessage`) in state history.

### 4.6. API Schemas & Route Integration

- **`app/api/schemas.py`**:
  - `ChatMessage(role: str, content: str)`
  - `Attachment(filename: str, content_type: str, url: str | None = None)`
  - `ChatRequest`: add `messages: list[ChatMessage] | None = None`, `attachments: list[Attachment] | None = None`.
  - `WorkflowListResponse`: returns `default: str`, `workflows: list[str]`, and `details: list[WorkflowInfo]`.
- **`app/api/routes.py`**:
  - Delegate execution entirely to `WorkflowRegistry.get(payload.workflow)`.
  - In `chat_event_generator`:
    - Lookup step labels from `workflow.info.step_labels`.
    - Check token emission against `workflow.info.generation_nodes`.
    - Filter out tool-call JSON deltas (`chunk.message.tool_call_chunks`).
    - Use `workflow.extract_eval_context()` for online evaluation.
    - Wrap execution with `workflow.handle_error()` for graceful failure synthesis.

---

## 5. Verification Plan

1. **Unit & Conformance Testing**:
   - `tests/rag/test_workflow_contracts.py`: Conformance suite running against `fix_single_llm`, `fix_single_jev`, and `react_agent`.
   - `tests/rag/test_tools.py`: Tool invocation, schema validation, and config/telemetry propagation.
   - `tests/rag/test_registry.py`: Registration, duplicate detection, lazy compilation, and lookup.
2. **API & Streaming Integration**:
   - Verify `GET /api/workflows` returns both legacy list and rich metadata.
   - Verify `POST /api/chat` and `POST /api/chat/stream` work seamlessly across all registered workflows.
   - Verify multi-turn context retention in `react_agent` using identical `session_id`.
   - Verify tool-call JSON chunks are not leaked during streaming.
3. **Telemetry & Observability**:
   - Confirm Langfuse trace generation, span hierarchy, and online evaluation execution for all workflows.
4. **Regression Safety**:
   - Run the complete existing test suite (260 tests) to ensure zero regressions on existing endpoints and workflows.
