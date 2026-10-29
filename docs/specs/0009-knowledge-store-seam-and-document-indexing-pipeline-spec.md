# Technical Specification: Knowledge Store Seam and Unified Document Indexing Pipeline

- **ADR Reference:** `docs/adr/0009-knowledge-store-seam-and-document-indexing-pipeline.md`
- **Target Version:** `0.9.0`
- **Status:** Draft / Ready for Implementation

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

## 2. Solution

Establish a unified knowledge subsystem in `app/rag/knowledge/` providing clean CQRS separation between the write pipeline (`DocumentIndexer`) and the read/retrieval storage seam (`VectorStoreAdapter`).

1. **Independent `VectorStoreAdapter` Protocol Seam (`app/rag/knowledge/base.py`)**:
   - Define a strongly-typed Python `Protocol` specifying dual synchronous and native asynchronous storage methods:
     - `add_documents` / `aadd_documents`: Persist chunks with optional deterministic IDs.
     - `similarity_search` / `asimilarity_search`: Retrieve top-$k$ relevant documents.
     - `as_retriever`: Return a LangChain-compatible retriever interface for `create_retriever_tool`.
   - Provide two concrete implementations:
     - `PineconeVectorStoreAdapter`: Production cloud adapter wrapping Pinecone Serverless with non-destructive validation and clean client injection.
     - `InMemoryVectorStoreAdapter`: Zero-dependency, offline in-memory vector store computing exact cosine similarity, equipped with a deterministic offline embedding double for 100% offline unit tests.
   - Provide an injectable factory `get_knowledge_store(settings=None, store_type=None)` managing singleton lifecycle and test overrides.

2. **Deep Write-Path Indexing Pipeline (`app/rag/knowledge/indexer.py`)**:
   - Encapsulate file format parsing, chunk splitting, batch scheduling, deterministic chunk ID generation, and vector persistence into a deep `DocumentIndexer` class.
   - Expose a minimal, high-leverage interface to callers:
     - `index_file(path)` / `aindex_file(path)`: Process a single file end-to-end.
     - `index_batch(paths)` / `aindex_batch(paths)`: Process multiple files with batched vector upserts and progress tracking.
   - Generate deterministic SHA-256 chunk IDs (`f"{source}:{chunk_index}:{content_hash}"`) to ensure idempotent upserts and prevent duplicate vectors.

3. **Pluggable Parser and Chunker Strategy Protocols (`app/rag/knowledge/parsers.py`, `app/rag/knowledge/chunkers.py`)**:
   - `DocumentParser`: Strategy protocol with `can_parse(suffix: str) -> bool` and `parse(path: Path) -> list[Document]`. Default registry supports PDF, Markdown, Plaintext, and DOCX.
   - `DocumentChunker`: Strategy protocol with `chunk(documents: list[Document]) -> list[Document]`. Default implementation wraps `RecursiveCharacterTextSplitter` configured via `app/core/config.py`.

4. **Caller Modernization & Backward Compatibility Shims**:
   - Migrate `app/api/routes.py:upload_file` to invoke `await indexer.aindex_file(dest)` directly, eliminating manual loader/chunker/batch orchestration.
   - Migrate `ingest_sample_kb.py` to invoke `indexer.index_batch(files)`.
   - Update `app/rag/tools/retriever.py` to obtain its retriever directly from `get_knowledge_store().as_retriever()`, removing all `sys.modules["app.rag.engine"]` hacks.
   - Preserve `app/rag/vectorstore.py` and `app/services/ingestion.py` as thin backward-compatibility delegation shims so existing tests and external scripts do not break.

---

## 3. User Stories

1. **As an API client**, I want `POST /api/upload` to index documents asynchronously using a single method call, so that file uploads are processed efficiently without threadpool starvation.
2. **As an operations engineer**, I want to run `python ingest_sample_kb.py` to ingest the company knowledge base through a unified batching pipeline, so that duplicate chunks are never created if I rerun the script.
3. **As a backend developer**, I want to swap vector database providers (e.g., from Pinecone to Qdrant or PGVector) by implementing `VectorStoreAdapter`, so that no workflow graphs, tool implementations, or API routes need to change.
4. **As a test engineer**, I want to configure `store_type="memory"` so that the entire RAG pipeline and document ingestion can run completely offline without Pinecone or OpenAI API keys.
5. **As an NLP engineer**, I want to swap the chunking strategy from recursive character splitting to semantic chunking by injecting a custom `DocumentChunker`, so that document boundary experiments do not leak into storage or caller code.
6. **As a systems architect**, I want `DocumentIndexer` to generate deterministic SHA-256 chunk IDs based on file source, chunk index, and content, so that re-indexing modified files is strictly idempotent.
7. **As an agent developer**, I want `create_retriever_tool` to obtain its retriever directly from the knowledge store seam, so that autonomous agents and DAG nodes query the same underlying store without reflection workarounds.
8. **As a maintainer**, I want existing unit tests importing `app.rag.vectorstore.get_retriever` and `app.services.ingestion.load_file` to continue working without modification, guaranteeing zero regression across the existing test suite.
9. **As a security engineer**, I want vectorstore initialization to validate index dimensions non-destructively rather than deleting existing cloud indexes on mismatch, preventing accidental data loss in production.
10. **As a developer**, I want `DocumentIndexer.aindex_file` to return a strongly-typed `IndexingResult` containing chunk counts, vector IDs created, and execution status, so that caller responses are structured and consistent.

---

## 4. Implementation Decisions & Component Specifications

### 4.1. Knowledge Store Seam (`app/rag/knowledge/base.py`)

#### 4.1.1. `VectorStoreAdapter` Protocol
```python
from typing import Any, Protocol, runtime_checkable
from langchain_core.documents import Document

@runtime_checkable
class VectorStoreAdapter(Protocol):
    """Protocol seam defining storage and similarity retrieval for knowledge documents."""

    def add_documents(self, documents: list[Document], ids: list[str] | None = None) -> list[str]:
        """Synchronously persist documents with optional deterministic IDs."""
        ...

    async def aadd_documents(self, documents: list[Document], ids: list[str] | None = None) -> list[str]:
        """Asynchronously persist documents with optional deterministic IDs."""
        ...

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        """Synchronously retrieve top-k documents matching the query."""
        ...

    async def asimilarity_search(self, query: str, k: int = 4) -> list[Document]:
        """Asynchronously retrieve top-k documents matching the query."""
        ...

    def as_retriever(self, search_kwargs: dict[str, Any] | None = None) -> Any:
        """Return a LangChain-compatible retriever object exposing invoke and ainvoke."""
        ...
```

#### 4.1.2. `IndexingResult` Model
```python
from pydantic import BaseModel, Field

class IndexingResult(BaseModel):
    """Normalized outcome returned by DocumentIndexer operations."""
    source: str = Field(..., description="File path or source identifier")
    document_count: int = Field(default=1, description="Number of parsed parent documents")
    chunk_count: int = Field(default=0, description="Total chunks created")
    ids_created: list[str] = Field(default_factory=list, description="List of vector IDs persisted")
    success: bool = Field(default=True, description="Whether indexing succeeded")
    error: str | None = Field(default=None, description="Error message if indexing failed")
```

---

### 4.2. Concrete Vector Store Adapters (`app/rag/knowledge/vectorstore.py`)

#### 4.2.1. `InMemoryVectorStoreAdapter`
- Maintains an in-memory dictionary of `chunk_id -> (Document, vector_float_list)`.
- If an embedding model (`OpenAIEmbeddings`) is supplied, invokes it for query and document embeddings.
- If `embeddings=None`, uses a deterministic offline hash embedding double (e.g., word-frequency hashing normalized to unit length), enabling 100% offline execution.
- Implements exact cosine similarity search over stored vectors:
  $$\text{sim}(u, v) = \frac{u \cdot v}{\|u\|_2 \|v\|_2}$$
- `as_retriever(search_kwargs)` returns a lightweight `VectorStoreRetriever` double supporting both synchronous `invoke(query)` and native asynchronous `ainvoke(query)`.

#### 4.2.2. `PineconeVectorStoreAdapter`
- Wraps `Pinecone` client and `PineconeVectorStore`.
- Validates index existence and dimension non-destructively; raises a clear `ConfigurationError` if the cloud index dimension does not match the configured embedding model rather than deleting the index.
- Implements `add_documents`, `aadd_documents`, `similarity_search`, `asimilarity_search`, and `as_retriever`.

#### 4.2.3. Factory Singleton (`get_knowledge_store`)
- Signature: `get_knowledge_store(settings: Settings | None = None, store_type: str | None = None) -> VectorStoreAdapter`
- Thread-safe singleton pattern using `threading.RLock()`.
- Resolves `store_type` from `settings.vector_store_type` (defaulting to `"pinecone"` in production and `"memory"` in tests or when Pinecone credentials are unconfigured).

---

### 4.3. Document Parsing and Chunking Strategies (`app/rag/knowledge/parsers.py`, `chunkers.py`)

#### 4.3.1. `DocumentParser` Protocol & Composite Parser
```python
@runtime_checkable
class DocumentParser(Protocol):
    def can_parse(self, suffix: str) -> bool: ...
    def parse(self, path: Path | str) -> list[Document]: ...
    async def aparse(self, path: Path | str) -> list[Document]: ...
```
- Built-in adapters: `PDFParser` (`PyPDFLoader`), `TextParser` (`TextLoader` for `.txt` and `.md`), `DocxParser` (`docx.Document`).
- Composite `CompositeDocumentParser` maintains an ordered registry of parsers and delegates by file extension.

#### 4.3.2. `DocumentChunker` Protocol & Recursive Splitter
```python
@runtime_checkable
class DocumentChunker(Protocol):
    def chunk(self, documents: list[Document]) -> list[Document]: ...
```
- Default `RecursiveCharacterChunker` wraps LangChain's `RecursiveCharacterTextSplitter`, parameterized by `chunk_size` and `chunk_overlap` from `Settings`.

---

### 4.4. Deep Write Pipeline (`app/rag/knowledge/indexer.py`)

- `DocumentIndexer`:
  - Constructor accepts `store: VectorStoreAdapter`, `parser: DocumentParser | None = None`, `chunker: DocumentChunker | None = None`, `batch_size: int = 10`.
  - Methods:
    - `index_file(path: Path | str) -> IndexingResult`
    - `aindex_file(path: Path | str) -> IndexingResult`
    - `index_batch(paths: list[Path | str]) -> list[IndexingResult]`
    - `aindex_batch(paths: list[Path | str]) -> list[IndexingResult]`
  - Execution steps:
    1. Parse raw file via `parser.parse(path)` / `aparse(path)`.
    2. Split into chunks via `chunker.chunk(docs)`.
    3. Generate deterministic IDs for each chunk:
       ```python
       content_hash = hashlib.sha256(chunk.page_content.encode("utf-8")).hexdigest()[:16]
       chunk_id = f"{Path(path).name}:{idx}:{content_hash}"
       ```
    4. Upsert chunks in batches into `store.add_documents(chunks, ids=ids)`.
    5. Return structured `IndexingResult`.

---

### 4.5. Caller Refactoring & Backward Compatibility

1. **`app/api/routes.py:upload_file`**:
   - Replaces the 15-line manual load/chunk/threadpool sequence with:
     ```python
     indexer = get_document_indexer()
     result = await indexer.aindex_file(dest)
     ```
2. **`ingest_sample_kb.py`**:
   - Refactored to:
     ```python
     indexer = get_document_indexer()
     results = indexer.index_batch(files)
     ```
3. **`app/rag/tools/retriever.py`**:
   - `create_retriever_tool`: Calls `get_knowledge_store().as_retriever()` when no retriever is injected, completely removing `sys.modules.get("app.rag.engine")` inspection.
4. **`app/rag/vectorstore.py` & `app/services/ingestion.py`**:
   - Preserved as backward-compatibility shims forwarding `get_retriever`, `add_documents`, `get_vectorstore`, `load_file`, `chunk_documents` to the new knowledge store components.

---

## 5. Testing Decisions

### 5.1. Principles of Good Tests
- **Test through the Seam, Not the Internals**: Tests verify inputs and outputs crossing the `VectorStoreAdapter` and `DocumentIndexer` seams. Internal parser classes, splitting mechanics, and raw cosine loops are verified through their behavioral outcomes (e.g. correct chunks retrieved, correct similarity ranking).
- **Zero Network Flakiness**: All unit tests run against `InMemoryVectorStoreAdapter` with offline embeddings. Tests never make network calls to Pinecone or OpenAI.
- **Strict Idempotency Verification**: Tests assert that re-indexing identical content produces identical IDs and zero duplicate chunks.
- **Dual Sync/Async Parity**: Every sync method has an equivalent async test verifying identical outputs.

### 5.2. Modules Tested & Test Suites
1. **`tests/rag/test_knowledge_store.py`**:
   - Verifies `InMemoryVectorStoreAdapter` document addition, similarity search, top-$k$ filtering, score ordering, and `as_retriever` compatibility with LangChain `invoke` / `ainvoke`.
2. **`tests/rag/test_document_indexer.py`**:
   - Verifies `index_file`, `aindex_file`, `index_batch`, and `aindex_batch` across PDF, Markdown, and TXT files.
   - Asserts deterministic chunk IDs and upsert idempotency.
3. **`tests/api/test_routes_upload.py`**:
   - Verifies HTTP `POST /api/upload` endpoint using FastAPI `TestClient` backed by `InMemoryVectorStoreAdapter`.
4. **`tests/test_indexing_regression.py`**:
   - Ensures legacy imports from `app.rag.vectorstore` and `app.services.ingestion` continue functioning with zero regressions.

---

## 6. Out of Scope

- Implementing third-party vector store adapters other than Pinecone and In-Memory (e.g. Qdrant, PGVector, Chroma) is deferred to future requirements; the seam is designed to accommodate them seamlessly.
- Implementing multimodal or vision-based document parsing is out of scope for this text-focused RAG release.
- Modifying prompt CMS (`app/services/prompts.py`) or LangGraph workflow topologies in `app/rag/engine.py`.

---

## 7. Further Notes

This technical specification establishes the Knowledge Store Seam conceived in ADR-0006 §3 and complements the BaseTool abstraction established in ADR-0008, bringing document ingestion up to the same enterprise modularity standard as the workflow and tool execution layers.
