# Technical Specification: Knowledge Store Seam and Unified Document Indexing Pipeline

- **ADR Reference:** `docs/adr/0009-knowledge-store-seam-and-document-indexing-pipeline.md`
- **Specification Document:** `docs/specs/0009-knowledge-store-seam-and-document-indexing-pipeline-spec.md`
- **Target Version:** `0.9.0`
- **Status:** Ready for Implementation

---

## 1. Problem Statement

Following ADR 0006, ADR 0007, and ADR 0008, the agentic RAG platform established a unified 10-node StateGraph engine, pluggable classifier adapters, an extensible multi-workflow registry, and a standardized dual-output `BaseTool` layer.

However, document ingestion and vector storage remain architecturally fragmented and shallow:
1. **Shallow Ingestion Layer**: `app/services/ingestion.py` exposes only two thin procedural helpers (`load_file` and `chunk_documents`), leaving all orchestration to callers. Callers such as the HTTP upload route (`app/api/routes.py:upload_file`) and bulk CLI ingesters (`ingest_sample_kb.py`) are forced to manually coordinate file reading, document loading, chunk splitting, threadpool dispatch, batch slicing, and vectorstore calls.
2. **Coupled Storage and Index Lifecycle**: `app/rag/vectorstore.py` tightly couples callers to `PineconeVectorStore` and cloud Pinecone SDKs. Its `ensure_index()` method executes blocking sleep loops (`while not pc.has_index: time.sleep(1)`) and destructively deletes indexes when embedding dimensions mismatch.
3. **Asymmetric Testability & Impossible Offline Testing**: Because vector storage is bound to cloud Pinecone and global module singletons (`_vectorstore`, `_embeddings`), testing ingestion or retrieval requires a live Pinecone API key or invasive monkeypatching of third-party network clients.
4. **Non-Idempotent Vector Ingestion**: Re-indexing an existing file assigns random chunk IDs, duplicating vector entries in Pinecone and degrading retrieval accuracy.
5. **Rigid Parsing and Chunking**: File extension handling and text chunking parameters (`chunk_size=900, chunk_overlap=120`) are hardcoded, preventing developers from testing alternative parsers or semantic chunkers without directly modifying library code.
6. **Seam Leakage in Retriever Tool**: `app/rag/tools/retriever.py` contains reflection hacks inspecting `sys.modules["app.rag.engine"]` to bypass global state when monkeypatched in legacy tests.

To achieve maximum modularity, callers require a deep document indexing pipeline behind a single-method interface, and workflows require an independent vector store protocol seam with an in-memory double for fast, offline testing.

---

## 2. Architectural Solution

Establish a unified knowledge subsystem in `app/rag/knowledge/` providing clean CQRS separation between the write pipeline (`DocumentIndexer`) and the read/retrieval storage seam (`VectorStoreAdapter`).

1. **Independent `VectorStoreAdapter` Protocol Seam (`app/rag/knowledge/base.py`)**:
   - Define a strongly-typed Python `Protocol` specifying dual synchronous and native asynchronous storage methods: `add_documents` / `aadd_documents`, `similarity_search` / `asimilarity_search`, and `as_retriever`.
   - Provide concrete `PineconeVectorStoreAdapter` and `InMemoryVectorStoreAdapter` implementations.
   - Provide an injectable factory `get_knowledge_store(settings=None, store_type=None)` managing singleton lifecycle and test overrides.
2. **Deep Write-Path Indexing Pipeline (`app/rag/knowledge/indexer.py`)**:
   - Encapsulate file parsing, chunk splitting, batch scheduling, deterministic SHA-256 chunk ID generation, and vector persistence behind a single-method `DocumentIndexer` interface (`index_file`, `aindex_file`, `index_batch`, `aindex_batch`).
3. **Pluggable Parser and Chunker Strategy Protocols (`parsers.py`, `chunkers.py`)**:
   - `DocumentParser`: Pluggable strategy with format registry (PDF, MD, TXT, DOCX).
   - `DocumentChunker`: Pluggable strategy driven by application settings.
4. **Caller Modernization & Backward Compatibility Shims**:
   - Migrate `app/api/routes.py:upload_file`, `ingest_sample_kb.py`, and `app/rag/tools/retriever.py`.
   - Preserve `app/rag/vectorstore.py` and `app/services/ingestion.py` as backward-compatibility delegation shims.

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
