# Work Packages / Implementation Tickets: Multi-Workflow Extensibility Platform

- **Specification Reference:** [`docs/specs/0007-multi-workflow-extensibility-platform-spec.md`](./0007-multi-workflow-extensibility-platform-spec.md)
- **ADR Reference:** [`docs/adr/0007-multi-workflow-extensibility-platform.md`](../adr/0007-multi-workflow-extensibility-platform.md)
- **Status:** In Progress

---

## Dependency Graph

```
┌─────────────────────────────────────────┐       ┌─────────────────────────────────────────┐
│ Ticket 01: Core Workflow Interfaces     │       │ Ticket 02: Tool Abstraction Layer       │
│ & Additive Schemas                      │       │ (app/rag/tools/)                        │
│ (app/rag/workflows/base.py, schemas.py) │       │                                         │
└────────────────────┬────────────────────┘       └────────────────────┬────────────────────┘
                     │                                                 │
          ┌──────────┴──────────┐                                      │
          ▼                     ▼                                      │
┌──────────────────┐   ┌──────────────────┐                            │
│ Ticket 03:       │   │ Ticket 04:       │                            │
│ WorkflowRegistry │   │ Conformance Test │                            │
│ (registry.py)    │   │ Harness Base     │                            │
└─────────┬────────┘   └────────┬─────────┘                            │
          │                     │                                      │
          ├─────────────────────┼──────────────────────────────────────┘
          │                     │
          ▼                     ▼
┌──────────────────┐   ┌────────────────────────────────────────────────┐
│ Ticket 05:       │   │ Ticket 06: Autonomous ReAct Agent Reference    │
│ Parameterized    │   │ Workflow with MemorySaver                      │
│ Self-Corrective  │   │ (app/rag/workflows/react_agent.py)             │
│ RAG Workflow     │   │                                                │
└─────────┬────────┘   └────────────────────────┬───────────────────────┘
          │                                     │
          └──────────────────┬──────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Ticket 07: Modernize Transport Layer & Streaming Token Filter         │
│ (app/api/routes.py, GET /api/workflows, chat_stream)                  │
└────────────────────────────────────┬──────────────────────────────────┘
                                     │
                                     ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Ticket 08: End-to-End Verification & Full Regression Test Suite       │
│ (Multi-turn conversational memory, telemetry, 260+ regression tests)  │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Core Workflow Interfaces & Additive Schemas | `app/rag/workflows/base.py`, `app/api/schemas.py`, `tests/rag/test_base_workflow.py` | None | `completed` |
| **Ticket 02** | Reusable Tool Abstraction Layer (`BaseTool`) | `app/rag/tools/retriever.py`, `app/rag/tools/search.py`, `app/rag/tools/__init__.py`, `tests/rag/test_tools.py` | None | `completed` |
| **Ticket 03** | Central Thread-Safe `WorkflowRegistry` | `app/rag/workflows/registry.py`, `tests/rag/test_registry.py` | None (Ticket 01 completed) | `completed` |
| **Ticket 04** | Reusable Conformance Test Harness | `tests/rag/test_workflow_contracts.py` | None (Ticket 01 completed) | `completed` |
| **Ticket 05** | Parameterized `SelfCorrectiveRagWorkflow` | `app/rag/workflows/self_corrective.py`, `app/rag/workflows/__init__.py`, `tests/rag/test_self_corrective_contract.py` | None (Tickets 01, 03, 04 completed) | `completed` |
| **Ticket 06** | Autonomous ReAct Agent Reference Workflow | `app/rag/workflows/react_agent.py`, `app/rag/workflows/__init__.py`, `tests/rag/test_react_agent.py` | None (Tickets 01, 02, 03, 04 completed) | `completed` |
| **Ticket 07** | Modernize Transport Layer & SSE Token Filtering | `app/api/routes.py`, `tests/api/test_routes_multi_workflow.py` | None (Tickets 03, 05, 06 completed) | `completed` |
| **Ticket 08** | End-to-End Verification & Regression Suite | `tests/test_chat_stream.py`, `tests/test_e2e_observability.py`, full suite | None (Ticket 07 completed) | `ready` |

---

## Ticket 01: Core Workflow Interfaces & Additive Schemas

- **Title:** Define Core Workflow Protocol Interfaces and Additive API Schemas
- **Target Files:**
  - `app/rag/workflows/base.py`
  - `app/api/schemas.py`
  - `tests/rag/test_base_workflow.py`
- **Blockers:** None
- **Status:** `completed`

### What to build:
Establish the structural contracts that all workflow implementations must satisfy. Define `WorkflowInfo`, `EvalContext`, and the runtime-checkable `WorkflowDefinition` Protocol. Additively extend `ChatRequest` to support optional multi-turn `messages` and `attachments` without breaking backward compatibility for `question: str`.

### Acceptance Criteria:
- [x] `WorkflowInfo` defined as a Pydantic model with fields:
  - `id: str`, `name: str`, `description: str`
  - `is_conversational: bool = False`
  - `supports_streaming: bool = True`
  - `step_labels: dict[str, str]`
  - `generation_nodes: set[str]`
- [x] `EvalContext` defined with `source_used: str = "direct"` and `retrieved_contexts: list[str] = []`.
- [x] `WorkflowDefinition` defined as `@runtime_checkable Protocol` requiring:
  - `info: WorkflowInfo`
  - `get_compiled_graph() -> Any`
  - `to_initial_state(request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]`
  - `to_chat_response(final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse`
  - `extract_eval_context(final_state: dict[str, Any]) -> EvalContext`
  - `handle_error(exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]`
- [x] `ChatMessage` and `Attachment` schemas added to `app/api/schemas.py`.
- [x] `ChatRequest` extended with optional `messages: list[ChatMessage] | None = None` and `attachments: list[Attachment] | None = None`. If `question` is provided, requests succeed as before.
- [x] `WorkflowListResponse` added with `default: str`, `workflows: list[str]`, and `details: list[WorkflowInfo]`.
- [x] Unit tests verify protocol runtime type checking and Pydantic validation.

---

## Ticket 02: Reusable Tool Abstraction Layer (`BaseTool`)

- **Title:** Extract Retrieval and Web Search into Typed LangChain `BaseTool` Factories
- **Target Files:**
  - `app/rag/tools/retriever.py`
  - `app/rag/tools/search.py`
  - `app/rag/tools/__init__.py`
  - `tests/rag/test_tools.py`
- **Blockers:** None
- **Status:** `completed`

### What to build:
Extract private knowledge base retrieval and Tavily web search into modular `BaseTool` instances with explicit Pydantic `args_schema` models. Ensure all tools propagate `RunnableConfig` for automatic Langfuse callback logging.

### Acceptance Criteria:
- [x] `KnowledgeSearchInput` defined with `query: str = Field(description="Search query to find internal documentation")`.
- [x] `create_retriever_tool(retriever=None) -> BaseTool` returns a LangChain tool named `"knowledge_base_search"` that queries Pinecone (or custom retriever) and formats page content with source metadata.
- [x] `WebSearchInput` defined with `query: str = Field(description="Search query for live external internet search")`.
- [x] `create_web_search_tool() -> BaseTool` returns a LangChain tool named `"web_search"` wrapping Tavily general search.
- [x] `app/rag/tools/__init__.py` re-exports the factory functions.
- [x] Unit tests verify tool schema validation, execution results, and metadata propagation.

---

## Ticket 03: Central Thread-Safe `WorkflowRegistry`

- **Title:** Implement Central WorkflowRegistry with Lazy Compilation and Warmup
- **Target Files:**
  - `app/rag/workflows/registry.py`
  - `tests/rag/test_registry.py`
- **Blockers:** Ticket 01
- **Status:** `completed`

### What to build:
Create a thread-safe registry that stores `WorkflowDefinition` instances, prevents duplicate registration, manages lazy singleton graph compilation, and supports pre-warming. Provide a `@register_workflow` decorator.

### Acceptance Criteria:
- [x] `WorkflowRegistry` class implements:
  - `register(workflow: WorkflowDefinition) -> None` (raises `ValueError` on duplicate IDs)
  - `get(workflow_id: str) -> WorkflowDefinition` (raises `KeyError` or `ValueError` if missing)
  - `list_ids() -> list[str]`
  - `list_info() -> list[WorkflowInfo]`
  - `warmup() -> None` (calls `get_compiled_graph()` on all registered workflows)
- [x] `@register_workflow` decorator allows declarative registration into the default registry.
- [x] Thread safety confirmed via concurrency tests simulating parallel lookups and compilation.
- [x] Unit tests cover registration, retrieval, listing, duplicate error handling, and warmup.

---

## Ticket 04: Reusable Conformance Test Harness

- **Title:** Build Reusable `BaseWorkflowContractTest` Test Suite
- **Target Files:**
  - `tests/rag/test_workflow_contracts.py`
- **Blockers:** Ticket 01
- **Status:** `completed`

### What to build:
Create an abstract test base class `BaseWorkflowContractTest` that tests any `WorkflowDefinition` against core platform contracts: state conversion, evaluation extraction, error fallback, and streaming node definitions. Include a `FakeWorkflow` double to test the harness.

### Acceptance Criteria:
- [x] `BaseWorkflowContractTest` provides test methods:
  - `test_workflow_info_validity()`
  - `test_to_initial_state_contract()`
  - `test_to_chat_response_contract()`
  - `test_extract_eval_context_contract()`
  - `test_handle_error_fallback()`
  - `test_compiled_graph_is_runnable()`
- [x] `FakeWorkflow` satisfies `WorkflowDefinition` and passes all base contract tests.
- [x] Demonstrates failing assertions when a dummy invalid workflow violates return shapes.

---

## Ticket 05: Parameterized `SelfCorrectiveRagWorkflow`

- **Title:** Implement Parameterized `SelfCorrectiveRagWorkflow` for `fix_single_llm` and `fix_single_jev`
- **Target Files:**
  - `app/rag/workflows/self_corrective.py`
  - `app/rag/workflows/__init__.py`
  - `tests/rag/test_self_corrective_contract.py`
- **Blockers:** Ticket 01, Ticket 03, Ticket 04
- **Status:** `completed`

### What to build:
Implement `SelfCorrectiveRagWorkflow` satisfying `WorkflowDefinition`. Encapsulate the 10-node DAG from `app/rag/engine.py`. Parameterize with `WorkflowClassifier`. Register both `"fix_single_llm"` (with `OpenAIClassifierAdapter`) and `"fix_single_jev"` (with `JevClassifierAdapter`) into the registry manifest.

### Acceptance Criteria:
- [x] `SelfCorrectiveRagWorkflow` takes `id`, `name`, `classifier`, and optional `retriever`.
- [x] `info` includes `generation_nodes = {"generate_from_kb", "generate_from_web", "direct_answer"}` and step labels matching existing UI spinners.
- [x] `to_initial_state` initializes full `AgentState` dictionary (`question`, `current_query`, `retry_count=0`, etc.).
- [x] `to_chat_response` constructs valid `ChatResponse` with `source_used`, `citations`, `trace`, and `answer`.
- [x] `extract_eval_context` returns `EvalContext(source_used=..., retrieved_contexts=...)`.
- [x] `handle_error` returns an `AgentState` dict with `source_used="error_fallback"` and user-friendly error answer.
- [x] Both instances registered in `app/rag/workflows/__init__.py`.
- [x] Inherits and passes `BaseWorkflowContractTest` for both classifier variants.

---

## Ticket 06: Autonomous ReAct Agent Reference Workflow

- **Title:** Implement Reference Autonomous ReAct Agent Workflow with `MemorySaver`
- **Target Files:**
  - `app/rag/workflows/react_agent.py`
  - `app/rag/workflows/__init__.py`
  - `tests/rag/test_react_agent.py`
- **Blockers:** Ticket 01, Ticket 02, Ticket 03, Ticket 04
- **Status:** `completed`

### What to build:
Implement `ReActAgentWorkflow` using `langgraph.prebuilt.create_react_agent`. Bind tools from `app/rag/tools/` (`retriever_tool` and `web_search_tool`). Attach `MemorySaver` checkpointer. Map `session_id` to `thread_id` so conversation history persists across calls. Extract tool citations and evaluate contexts.

### Acceptance Criteria:
- [x] `ReActAgentWorkflow` satisfies `WorkflowDefinition` with `id="react_agent"` and `is_conversational=True`.
- [x] Graph compiled with `create_react_agent(llm, tools=[...], checkpointer=MemorySaver())`.
- [x] `to_initial_state` maps `request.question` and optional `request.messages` into a message list (`HumanMessage`, `AIMessage`).
- [x] `to_chat_response` parses final message sequence: extracts final AI message as `answer`, parses `ToolMessage` outputs into `citations` and `retrieved_contexts`, and sets `source_used="react_agent"`.
- [x] `handle_error` provides a graceful fallback response when a tool throws.
- [x] Multi-turn test demonstrates that calling the workflow twice with the same `session_id` remembers facts stated in turn 1 without re-passing messages.
- [x] Passes `BaseWorkflowContractTest`.

---

## Ticket 07: Modernize Transport Layer & SSE Token Filtering

- **Title:** Modernize FastAPI Transport Layer, Capability Introspection & SSE Stream Filtering
- **Target Files:**
  - `app/api/routes.py`
  - `tests/api/test_routes_multi_workflow.py`
- **Blockers:** Ticket 03, Ticket 05, Ticket 06
- **Status:** `completed`

### What to build:
Refactor `app/api/routes.py` to route requests exclusively through `WorkflowRegistry`. Enrich `GET /api/workflows` to return `WorkflowListResponse` with rich capability metadata. In `chat_event_generator`, dynamically resolve step labels and generation nodes from the workflow, and filter out tool-call argument JSON tokens during ReAct execution.

### Acceptance Criteria:
- [x] `GET /api/workflows` returns `{ "default": "fix_single_jev", "workflows": [...], "details": [...] }`.
- [x] `POST /api/chat` retrieves workflow from `WorkflowRegistry.get(payload.workflow)`, calls `to_initial_state()`, executes graph with `configurable: {"thread_id": session_id}`, and returns `to_chat_response()`.
- [x] `POST /api/chat/stream` streams events using dynamic `workflow.info.step_labels` and `workflow.info.generation_nodes`.
- [x] SSE generator filters out token deltas when `chunk.message.tool_call_chunks` is present or text is empty, ensuring tool JSON arguments are never streamed to clients.
- [x] Stream generator invokes `workflow.handle_error()` on unhandled exceptions, yielding a clean terminal metadata event or error event.
- [x] `run_online_evaluation_task` receives context from `workflow.extract_eval_context(final_state)`.
- [x] Unit and API tests confirm dynamic routing and streaming across all registered workflows.

---

## Ticket 08: End-to-End Verification & Full Regression Test Suite

- **Title:** End-to-End Multi-Workflow Verification & Full Regression Test Suite
- **Target Files:**
  - `tests/test_chat_stream.py`
  - `tests/test_e2e_observability.py`
  - Entire test suite (260+ tests)
- **Blockers:** None (Ticket 07 completed)
- **Status:** `ready`

### What to build:
Verify end-to-end integration across all workflows (`fix_single_llm`, `fix_single_jev`, and `react_agent`). Ensure full backward compatibility for existing scripts, Langfuse trace structures, and run the entire test suite to guarantee zero regressions.

### Acceptance Criteria:
- [ ] All existing 260 tests pass without modification.
- [ ] New E2E tests verify switching between `fix_single_jev` and `react_agent` via `/api/chat` and `/api/chat/stream`.
- [ ] Streaming tokens for `react_agent` verified to contain only conversational text, with tool invocations emitting `step` events.
- [ ] Multi-turn session persistence verified end-to-end via HTTP requests sharing `session_id`.
- [ ] Langfuse trace spans verified for both fixed DAG workflows and ReAct tool executions.
- [ ] Online evaluation scores successfully recorded for all workflows without schema errors.
