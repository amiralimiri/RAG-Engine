# ADR 0008: Unified BaseTool Abstraction Seam for Workflow Engine and Autonomous Agents

## Status
Accepted (Extends ADR 0006 and ADR 0007)

## Context
In ADR 0006 and ADR 0007, we unified the self-corrective RAG engine into a single 10-node StateGraph (`app/rag/engine.py`) and introduced a reusable tool abstraction layer (`app/rag/tools/`) for autonomous ReAct agents.

However, an architectural fracture remained between fixed DAG workflows and autonomous agents:
1. **Duplicate Provider Logic**: `app/rag/engine.py` directly called `TavilySearch` and `get_retriever()`, re-implementing bespoke JSON parsing, string formatting, and citation extraction rather than using `app/rag/tools/`.
2. **Asymmetric Testability**: Autonomous agents interacted with typed `BaseTool` mocks, while testing the fixed DAG required mocking raw retriever objects and global search singletons.
3. **Synchronous I/O in Async Graph**: While generation nodes were asynchronous (`ainvoke`), `retrieve_kb` and `search_web` were synchronous (`invoke`), blocking event dispatch during token streaming.

---

## Decisions

### 1. Canonical `BaseTool` Protocol with Dual Output
- Adopt LangChain's `BaseTool` protocol configured with `response_format="content_and_artifact"` as the sole canonical interface for enterprise retrieval and web search across all workflows.
- Standardize tool outputs:
  - **Knowledge Base Retrieval Tool (`create_retriever_tool`)**:
    - `content: str`: Formatted document text chunks for prompt synthesis.
    - `artifact: dict[str, Any]`: Structured dictionary containing `{"documents": list[Document], "citations": list[dict[str, Any]]}` for direct `AgentState` mapping.
  - **Web Search Tool (`create_web_search_tool`)**:
    - `content: str`: Aggregated web snippet text and answer summary.
    - `artifact: dict[str, Any]`: Structured dictionary containing `{"citations": list[dict[str, Any]], "retrieved_contexts": list[str]}` for citations and online evaluation.

### 2. Dependency Injection at the Workflow Seam
- Update `create_agentic_rag_graph` and `SelfCorrectiveRagWorkflow` to accept explicit `kb_tool: BaseTool | None = None` and `web_tool: BaseTool | None = None` parameters.
- Provide default factories (`create_retriever_tool()` and `create_web_search_tool()`) when parameters are omitted.
- Maintain backward compatibility for the legacy `retriever: Any = None` parameter by wrapping it in `create_retriever_tool(retriever=retriever)` if `kb_tool` is not explicitly provided.

### 3. Native Asynchronous Execution
- Promote graph nodes `retrieve_kb` and `search_web` in `app/rag/engine.py` to native `async def` functions invoking `await tool.ainvoke(..., config=config)`.
- Tools expose both synchronous `_run` and native asynchronous `_arun` implementations to support both sync batch operations and non-blocking streaming runs.

### 4. Transparent Error Propagation
- Provider exceptions (e.g. Pinecone network failures or Tavily rate limits) bubble up through the tool layer directly to LangGraph rather than being swallowed as empty results.
- Failures trigger the centralized `WorkflowDefinition.handle_error` lifecycle hook and record structured error telemetry in Langfuse.

---

## Consequences

### Positive
- **Locality**: Parsing, formatting, and citation generation concentrate inside `app/rag/tools/`. Changes to Tavily output formats or citation structures update in one place.
- **Leverage**: Fixed DAGs (`SelfCorrectiveRagWorkflow`) and autonomous agents (`ReActAgentWorkflow`) share identical tool implementations, telemetry callbacks, and mock doubles.
- **Testability**: The entire workflow engine can be verified using lightweight `BaseTool` fakes without network I/O or global singleton patching.
- **Performance**: Non-blocking `ainvoke` calls across all I/O nodes eliminate threadpool blocking during streaming token generation.

### Negative
- **LangChain Tool Dependency**: Workflows must interact with the `BaseTool` interface, tying tools to LangChain's abstraction rather than bare Python callables.
- **Dual-Output Artifact Unpacking**: Calling code must unpack the tuple `(content, artifact)` or read `tool_message.artifact` when invoking tools directly.
