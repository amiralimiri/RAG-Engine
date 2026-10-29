# ADR 0007: Multi-Workflow Extensibility Platform

## Status
Accepted (Extends ADR 0006)

## Context
ADR 0006 successfully unified the self-corrective RAG topology and established the `WorkflowClassifier` seam (`app/rag/classifiers.py`), allowing `fix_single_llm` and `fix_single_jev` to share the same 10-node StateGraph engine.

However, the platform remained constrained to a single fixed-graph topology:
1. **Coupled Transport & State**: `app/api/routes.py` and `app/rag/engine.py` hardcode module-level dictionaries (`WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, `_COMPILED_GRAPHS`), generation node whitelists (`GENERATION_NODES`), and step UI labels (`STEP_LABELS`).
2. **Monolithic State Schema**: The execution runtime explicitly assumes all workflows run on `AgentState` (`kb_docs`, `web_grade`, `retry_count`, `citations`), making it impossible to register conversational ReAct agents, dynamic tool-calling loops, multi-turn chat workflows with memory, or multimodal pipelines.
3. **Hardcoded Tool Bindings**: Retrieval (`retrieve_kb`) and search (`search_web`) exist only as bespoke functions inside the 10-node DAG rather than reusable, typed `BaseTool` components usable by autonomous agents.
4. **Rigid API Contracts**: `ChatRequest` only accepts a single string `question`, omitting multi-turn messages or attachments, and `GET /api/workflows` returns only a bare string list without workflow capability metadata.
5. **Brittle Streaming**: Token streaming assumes every workflow emits text directly from three specific generation nodes, leaking raw tool-call JSON chunks when autonomous agents invoke tools.

To scale the platform into an enterprise extensible multi-agent system, developers need an open-closed architecture to register, test, and dynamically swap arbitrary workflow topologies without touching FastAPI transport code.

---

## Decisions

### 1. Strongly Typed `WorkflowDefinition` Protocol & `WorkflowRegistry`
- Define a formal `WorkflowDefinition` protocol in `app/rag/workflows/base.py`:
  - `info: WorkflowInfo`: Structured metadata declaring `id`, `name`, `description`, `is_conversational`, `supports_streaming`, `step_labels`, and `generation_nodes`.
  - `get_compiled_graph() -> Any`: Thread-safe, lazily compiled LangGraph runnable singleton.
  - `to_initial_state(request: ChatRequest, trace_id: str | None) -> dict[str, Any]`: Normalizes inbound API requests into workflow-private graph state.
  - `to_chat_response(final_state: dict[str, Any], trace_id: str | None) -> ChatResponse`: Adapts internal workflow state back to the standard API response schema.
  - `extract_eval_context(final_state: dict[str, Any]) -> EvalContext`: Extracts normalized `source_used` and `retrieved_contexts` for online evaluation.
  - `handle_error(exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]`: Fallback hook providing graceful degradation upon unexpected node failures.
- Implement a thread-safe singleton `WorkflowRegistry` in `app/rag/workflows/registry.py` with an explicit registration manifest in `app/rag/workflows/__init__.py` and a `@register_workflow` decorator.
- Provide an optional startup warm-up hook to compile registered graphs ahead of incoming HTTP traffic.

### 2. State Normalization & Boundary Inversion
- Decouple internal workflow state representations from the API transport layer. Workflows own their private state schemas:
  - `SelfCorrectiveRagWorkflow` operates on `AgentState`.
  - `ReActAgentWorkflow` operates on LangGraph message lists (`Sequence[BaseMessage]`).
- The API layer interacts exclusively with `ChatRequest`, `ChatResponse`, and typed SSE streaming events, relying on the workflow's bidirectional adapters.

### 3. Reusable Tool Abstraction Layer (`app/rag/tools/`)
- Extract retrieval and web search into typed LangChain `BaseTool` factories:
  - `create_retriever_tool(retriever=None)`: Wraps Pinecone vectorstore queries with a Pydantic `KnowledgeSearchInput` schema.
  - `create_web_search_tool()`: Wraps Tavily web search with a Pydantic `WebSearchInput` schema.
- Tools automatically inherit Langfuse telemetry propagation and structured execution logging through standard LangChain callback handlers.

### 4. Dynamic Checkpointers & Multi-Turn State Persistence
- When `ChatRequest.session_id` is provided, the execution runtime automatically injects `{"configurable": {"thread_id": session_id}}` into `RunnableConfig`.
- Stateful workflows (such as ReAct and multi-turn conversational agents) leverage LangGraph checkpointers (`MemorySaver` or pluggable persistent checkpointers) to maintain conversation scratchpads across turns without requiring clients to retransmit entire chat histories.

### 5. Selective Token Streaming & SSE Normalization
- SSE generator inspects `workflow.info.generation_nodes` and `workflow.info.step_labels` dynamically.
- Filter out tool-call argument deltas (`chunk.message.tool_call_chunks`) during ReAct execution so only user-facing prose tokens stream to the UI.

### 6. Rich Workflow Introspection & Additive API Contracts
- Expand `ChatRequest` additively with optional `messages: list[ChatMessage] | None = None` and `attachments: list[Attachment] | None = None` while preserving `question: str` for 100% backward compatibility.
- Enrich `GET /api/workflows` to return full `WorkflowInfo` capabilities alongside the legacy `default` and `workflows` string list.

### 7. Parameterized Self-Corrective RAG & Reference ReAct Agent
- Consolidate `fix_single_llm` and `fix_single_jev` as registered instances of a single `SelfCorrectiveRagWorkflow` class parameterized by `OpenAIClassifierAdapter` and `JevClassifierAdapter`.
- Implement and register `ReActAgentWorkflow` as an empirical proof-of-extensibility, leveraging the new tool layer and conversational memory.

### 8. Conformance Testing Harness (`BaseWorkflowContractTest`)
- Provide a standardized test base class in `tests/rag/test_workflow_contracts.py` verifying state mapping, error fallbacks, eval context extraction, and streaming node definitions for all registered workflows.

---

## Consequences

### Positive
- **Open-Closed Principle**: New workflows (multimodal, multi-agent, tree-of-thought) can be introduced by creating a single class in `app/rag/workflows/` without modifying `routes.py`, `engine.py`, or API schemas.
- **Topology Agnostic**: Dynamic tool calling and fixed DAGs live side-by-side with identical telemetry, streaming, and evaluation guarantees.
- **Developer Experience**: Clear protocol boundaries and reusable test harnesses make building, registering, and testing new workflows fast and reliable.
- **Backward Compatibility**: Existing clients and test suites using `fix_single_llm` and `fix_single_jev` continue working with zero regression.

### Negative
- **Abstraction Depth**: Introducing `WorkflowDefinition`, `WorkflowRegistry`, and state adapters adds architectural layering compared to ad-hoc dictionary lookups.
- **State Conversion Overhead**: Small CPU overhead for converting between API models and internal graph states, negligible compared to LLM and network latency.
