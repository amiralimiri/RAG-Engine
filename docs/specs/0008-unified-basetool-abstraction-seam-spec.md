# Technical Specification: Unified BaseTool Abstraction Seam for Workflow Engine and Autonomous Agents

- **ADR Reference:** `docs/adr/0008-unified-basetool-abstraction-seam.md`
- **Target Version:** `0.8.0`
- **Status:** Draft / Ready for Implementation

---

## 1. Problem Statement

Following ADR 0006 and ADR 0007, the self-corrective RAG engine consolidated its 10-node topology into a single StateGraph (`app/rag/engine.py`) and introduced a reusable tool abstraction layer (`app/rag/tools/`) for the autonomous ReAct agent (`ReActAgentWorkflow`).

However, an architectural fracture remains between fixed DAG workflows and autonomous agents:
1. **Duplicate Provider Logic**: `app/rag/engine.py` directly invokes `TavilySearch` and Pinecone's `get_retriever()`, re-implementing custom JSON parsing, string formatting, and citation extraction rather than reusing the tools in `app/rag/tools/`.
2. **Asymmetric Testability**: Autonomous agents interact with typed `BaseTool` mocks, while testing the fixed DAG requires mocking raw retriever objects and patching global search client singletons.
3. **Synchronous I/O in Async Graph**: While query rewrite and generation nodes are asynchronous (`ainvoke`), `retrieve_kb` and `search_web` are synchronous (`invoke`), blocking event dispatch during SSE token streaming.
4. **Information Discard in Tool Artifacts**: Current tool implementations discard critical metadata (e.g. `create_retriever_tool` drops `list[Document]`, returning only citation dictionaries), preventing DAG nodes from consuming tool results directly without re-retrieval.

To establish complete parity across all workflows, the platform requires a single, deep tool seam where both deterministic DAG nodes and autonomous agents consume identical `BaseTool` instances delivering strongly-typed artifacts.

---

## 2. Solution

Consolidate all enterprise knowledge retrieval and external web search behind LangChain's `BaseTool` protocol configured with dual-output `response_format="content_and_artifact"`.

1. **Standardize Tool Artifact Contracts (`app/rag/tools/`)**:
   - **Knowledge Base Retrieval (`create_retriever_tool`)**: Emits formatted document text as `content`, and `{"documents": list[Document], "citations": list[dict[str, Any]]}` as `artifact`.
   - **Web Search (`create_web_search_tool`)**: Emits formatted search prose as `content`, and `{"citations": list[dict[str, Any]], "retrieved_contexts": list[str]}` as `artifact`.
   - Both tools provide native synchronous (`_run`) and asynchronous (`_arun`) handlers, propagating provider exceptions transparently to LangGraph.
2. **Dependency Injection in Workflow Engine (`app/rag/engine.py`)**:
   - Update `create_agentic_rag_graph` to accept `kb_tool: BaseTool | None = None` and `web_tool: BaseTool | None = None`.
   - Promote `retrieve_kb` and `search_web` to native `async def` nodes using `await tool.ainvoke(...)`.
   - Map tool artifacts directly to `AgentState` attributes (`kb_docs`, `web_results`, `citations`, `retrieved_contexts`) without redundant parsing.
   - Retain `retriever: Any = None` as a backward-compatible adapter that wraps legacy mock retrievers via `create_retriever_tool(retriever=retriever)`.
3. **Parameterize Self-Corrective Workflow (`app/rag/workflows/self_corrective.py`)**:
   - Expand `SelfCorrectiveRagWorkflow.__init__` to accept `kb_tool: BaseTool | None = None` and `web_tool: BaseTool | None = None`.
   - Forward both injected tools to `create_agentic_rag_graph`.
4. **Clean Tool Import Surface (`app/rag/tools/__init__.py`)**:
   - Re-export `create_retriever_tool`, `create_web_search_tool`, `KnowledgeSearchInput`, and `WebSearchInput`.

---

## 3. User Stories

1. **As a workflow author**, I want to build graph nodes that invoke `BaseTool` instances directly, so that I don't have to write custom client wrappers, parsing code, or citation extractors.
2. **As an autonomous agent author**, I want `ReActAgentWorkflow` and `SelfCorrectiveRagWorkflow` to consume identical tool implementations, so that behavioral improvements or bug fixes in search/retrieval apply everywhere automatically.
3. **As a test engineer**, I want to inject fake `BaseTool` test doubles into `create_agentic_rag_graph` and `SelfCorrectiveRagWorkflow`, so that I can run end-to-end graph tests without real network I/O or global singleton patching.
4. **As an SSE streaming client**, I want retrieval and search nodes to run as non-blocking `async def` coroutines, so that event streaming and token delivery are never blocked by threadpool synchronization.
5. **As an LLMOps engineer**, I want `retrieve_kb` and `search_web` to populate `retrieved_contexts` cleanly through tool artifacts, so that online DeepEval faithfulness evaluation receives complete evidence across all workflows.
6. **As an operations engineer**, I want third-party API failures (e.g. Pinecone timeouts or Tavily 429s) to bubble up to LangGraph, so that `handle_error` hooks record the failure in Langfuse and return graceful fallback responses.
7. **As a maintainer**, I want existing unit tests passing `retriever=mock_retriever` to continue passing without modification, ensuring zero regression across the existing test suite.

---

## 4. Implementation Decisions & Specifications

### 4.1. Seam 1: Tool Abstraction Layer (`app/rag/tools/`)

#### 4.1.1. Knowledge Base Retrieval Tool (`app/rag/tools/retriever.py`)

- **Factory Signature**:
  ```python
  def create_retriever_tool(
      retriever: Any = None,
      name: str = "knowledge_base_search",
      description: str = (
          "Search the internal company knowledge base and IT documentation for technical policies, "
          "VPN, hardware, software installation, and account setup guidelines."
      ),
  ) -> BaseTool:
      ...
  ```
- **Dual-Output Return Contract**:
  - `content: str`: Formatted markdown text with document content and source headers: `[Source: <filename>]\n<content>`.
  - `artifact: dict[str, Any]`: Structured dictionary:
    ```python
    {
        "documents": list[Document],
        "citations": list[dict[str, Any]],  # [{"title": str, "url": "", "type": "private_kb"}]
    }
    ```
- **Async & Sync Behavior**:
  - `_run(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]`
  - `_arun(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]`
  - Resolves active retriever lazily: uses injected `retriever` if provided, else imports and calls `app.rag.vectorstore.get_retriever()`.
  - Binds `structlog.contextvars.bind_contextvars(step="retrieve_kb")`.
  - Exceptions from `retriever.invoke` or `retriever.ainvoke` bubble up directly.

#### 4.1.2. Web Search Tool (`app/rag/tools/search.py`)

- **Factory Signature**:
  ```python
  def create_web_search_tool(
      search_client: Any = None,
      name: str = "web_search",
      description: str = (
          "Search the public web for external technical documentation, official software manuals, "
          "and internet troubleshooting resources when internal documentation is absent or insufficient."
      ),
  ) -> BaseTool:
      ...
  ```
- **Dual-Output Return Contract**:
  - `content: str`: Aggregated search answer and result content snippets.
  - `artifact: dict[str, Any]`: Structured dictionary:
    ```python
    {
        "citations": list[dict[str, Any]],     # [{"title": str, "url": str, "type": "web"}]
        "retrieved_contexts": list[str],       # ["Title: ...\nURL: ...\nContent: ...", ...]
    }
    ```
- **Async & Sync Behavior**:
  - `_run(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]`
  - `_arun(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]`
  - Resolves active Tavily client lazily via `get_settings().tavily_api_key`.
  - Binds `structlog.contextvars.bind_contextvars(step="search_web")`.
  - Exceptions from Tavily client bubble up directly.

---

### 4.2. Seam 2: Consolidated Workflow Engine (`app/rag/engine.py`)

#### 4.2.1. Graph Factory Signature

```python
def create_agentic_rag_graph(
    classifier: WorkflowClassifier,
    retriever: Any = None,
    *,
    kb_tool: BaseTool | None = None,
    web_tool: BaseTool | None = None,
) -> Any:
    ...
```

- **Tool Resolution Logic**:
  ```python
  # Resolve KB Tool
  if kb_tool is not None:
      active_kb_tool = kb_tool
  elif retriever is not None:
      from app.rag.tools import create_retriever_tool
      active_kb_tool = create_retriever_tool(retriever=retriever)
  else:
      from app.rag.tools import create_retriever_tool
      active_kb_tool = create_retriever_tool()

  # Resolve Web Tool
  if web_tool is not None:
      active_web_tool = web_tool
  else:
      from app.rag.tools import create_web_search_tool
      active_web_tool = create_web_search_tool()
  ```

#### 4.2.2. Promoted Async I/O Nodes

- **`retrieve_kb` Node**:
  ```python
  async def node_retrieve_kb(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
      structlog.contextvars.bind_contextvars(step="retrieve_kb")
      # Invoke tool via astream/ainvoke
      raw_output = await active_kb_tool.ainvoke({"query": state["current_query"]}, config=config)
      
      # Unpack dual-output tuple or ToolMessage
      if isinstance(raw_output, tuple) and len(raw_output) == 2:
          content, artifact = raw_output
      elif hasattr(raw_output, "artifact") and raw_output.artifact:
          content, artifact = raw_output.content, raw_output.artifact
      else:
          content, artifact = str(raw_output), {}

      docs = artifact.get("documents", []) if isinstance(artifact, dict) else []
      citations = artifact.get("citations", []) if isinstance(artifact, dict) else []
      retrieved_contexts = [d.page_content for d in docs] if docs else ([content] if content else [])

      return {
          "kb_docs": docs,
          "retrieved_contexts": retrieved_contexts,
          "citations": citations,
          "trace": [f"Private KB retrieval → {len(docs)} chunks"],
      }
  ```

- **`search_web` Node**:
  ```python
  async def node_search_web(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
      structlog.contextvars.bind_contextvars(step="search_web")
      raw_output = await active_web_tool.ainvoke({"query": state["current_query"]}, config=config)
      
      if isinstance(raw_output, tuple) and len(raw_output) == 2:
          content, artifact = raw_output
      elif hasattr(raw_output, "artifact") and raw_output.artifact:
          content, artifact = raw_output.content, raw_output.artifact
      else:
          content, artifact = str(raw_output), {}

      citations = artifact.get("citations", []) if isinstance(artifact, dict) else []
      contexts = artifact.get("retrieved_contexts", []) if isinstance(artifact, dict) else []

      return {
          "web_results": content,
          "citations": citations,
          "source_used": "web",
          "retrieved_contexts": contexts,
          "trace": ["Web fallback → Tavily search"],
      }
  ```

#### 4.2.3. Cleanup in `app/rag/engine.py`

- Delete redundant module-level `_web_search` and `web_search_tool()`.
- Delete duplicated Tavily parsing logic in `search_web`.
- Delete duplicated vectorstore retrieval in `retrieve_kb`.

---

### 4.3. Seam 3: Parameterized Self-Corrective Workflow (`app/rag/workflows/self_corrective.py`)

- **Class Signature**:
  ```python
  class SelfCorrectiveRagWorkflow:
      def __init__(
          self,
          id: str,
          name: str,
          classifier: WorkflowClassifier,
          retriever: Any = None,
          *,
          kb_tool: BaseTool | None = None,
          web_tool: BaseTool | None = None,
          description: str | None = None,
          step_labels: dict[str, str] | None = None,
          generation_nodes: set[str] | None = None,
          metadata: dict[str, Any] | None = None,
      ) -> None:
          self.classifier = classifier
          self.retriever = retriever
          self.kb_tool = kb_tool
          self.web_tool = web_tool
          ...
  ```
- **Graph Compilation Hook**:
  ```python
  def get_compiled_graph(self) -> Any:
      with self._lock:
          if self._compiled_graph is None:
              self._compiled_graph = create_agentic_rag_graph(
                  classifier=self.classifier,
                  retriever=self.retriever,
                  kb_tool=self.kb_tool,
                  web_tool=self.web_tool,
              )
          return self._compiled_graph
  ```

---

## 5. Testing Decisions (TDD Acceptance Criteria)

### 5.1. Testing Principles
- Test through the public interface of the tools (`tool.invoke`, `tool.ainvoke`) and the compiled graph (`graph.invoke`, `graph.ainvoke`).
- Do not mock internal helper functions; inject fake retrievers or fake search clients into the tool factories, or inject fake `BaseTool` instances directly into `create_agentic_rag_graph`.

### 5.2. Test Suite 1: Tool Seam Verification (`tests/rag/test_tools.py`)
1. **Retriever Tool Dual-Output Contract**:
   - Given a mock retriever returning `[Document(page_content="VPN info", metadata={"source": "docs/vpn.md"})]`.
   - When calling `tool.invoke({"query": "vpn"})` or `await tool.ainvoke({"query": "vpn"})`.
   - Then `content` must contain `"[Source: docs/vpn.md]\nVPN info"`.
   - And `artifact["documents"]` must contain the original `Document` list.
   - And `artifact["citations"]` must contain `[{"title": "vpn.md", "url": "", "type": "private_kb"}]`.
2. **Web Search Tool Dual-Output Contract**:
   - Given a mock Tavily client returning `{"answer": "Slack guides", "results": [{"title": "Slack", "url": "https://slack.com", "content": "Help text"}]}`.
   - When calling `tool.invoke({"query": "slack"})` or `await tool.ainvoke({"query": "slack"})`.
   - Then `content` must contain `"Search answer: Slack guides"`.
   - And `artifact["citations"]` must contain `[{"title": "Slack", "url": "https://slack.com", "type": "web"}]`.
   - And `artifact["retrieved_contexts"]` must contain `"Title: Slack\nURL: https://slack.com\nContent: Help text"`.
3. **Transparent Exception Propagation**:
   - Given a retriever or search client that raises `ConnectionError("Timeout")`.
   - When calling `tool.invoke` or `await tool.ainvoke`.
   - Then `ConnectionError` must be raised (not swallowed).

### 5.3. Test Suite 2: Engine Integration with Tool Seam (`tests/test_workflow_engine.py`)
1. **Injected Custom Tools**:
   - Pass custom `kb_tool` and `web_tool` into `create_agentic_rag_graph`.
   - Verify that executing the graph invokes the custom tools and correctly populates `state["kb_docs"]`, `state["citations"]`, and `state["web_results"]`.
2. **Backward-Compatibility with Legacy `retriever` Argument**:
   - Pass `retriever=mock_retriever` without `kb_tool`.
   - Verify graph compiles and retrieves docs through the wrapped tool seamlessly.
3. **Async Node Execution in Streaming Pipeline**:
   - Verify `astream_events("v2")` captures `on_tool_end` / `on_chain_end` events for both `retrieve_kb` and `search_web`.

### 5.4. Test Suite 3: Workflow Conformance (`tests/rag/test_self_corrective_contract.py`)
- Verify that `SelfCorrectiveRagWorkflow` initialized with custom `kb_tool` and `web_tool` passes all `BaseWorkflowContractTest` checks.

---

## 6. Out of Scope

- Migrating document ingestion (`/api/ingest`) to a deep module (deferred to Candidate 1).
- Encapsulating SSE stream translation out of `app/api/routes.py` (deferred to Candidate 3).
- Removing legacy dictionary monkeypatch shims (`WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`) (deferred to Candidate 4).
- Adding new external tool providers (e.g., Google Search, Confluence, Jira).

---

## 7. Migration & Rollout Plan

1. **Step 1 (Red)**: Update `tests/rag/test_tools.py` with failing tests asserting the new dual-output artifact structure for both tools.
2. **Step 2 (Green)**: Update `app/rag/tools/retriever.py` and `app/rag/tools/search.py` to produce the enriched artifact bundles.
3. **Step 3 (Red)**: Add tests in `tests/test_workflow_engine.py` verifying `create_agentic_rag_graph` with injected `kb_tool` and `web_tool`.
4. **Step 4 (Green)**: Refactor `app/rag/engine.py` to accept `kb_tool` and `web_tool`, promote `retrieve_kb` and `search_web` to `async def`, and remove duplicate Tavily search parsing.
5. **Step 5 (Green)**: Update `app/rag/workflows/self_corrective.py` to accept and forward `kb_tool` and `web_tool`.
6. **Step 6 (Verify)**: Run the full test suite (`pytest`) to ensure 100% backward compatibility.
