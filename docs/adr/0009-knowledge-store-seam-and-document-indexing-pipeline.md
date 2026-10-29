# ADR 0009: Knowledge Store Seam and Unified Document Indexing Pipeline

## Status
Accepted (Extends ADR 0006 and ADR 0008)

## Context
Document ingestion and vector persistence were previously scattered across shallow procedural helpers (`app/services/ingestion.py` and `app/rag/vectorstore.py`). Callers (`POST /api/upload` in `routes.py` and CLI script `ingest_sample_kb.py`) were forced to coordinate file loading, text chunking, threadpools, batching, and raw Pinecone cloud calls directly. Furthermore, vectorstore operations relied on mutable module globals and blocking network polling loops, making offline testing impossible without mocking third-party network clients.

To realize the pluggable knowledge store architecture projected in ADR-0006 §3 and ADR-0008 §2, we need a clean CQRS separation: an independent vector storage protocol seam with in-memory test doubles, and a deep write-path indexing pipeline that hides chunking and parsing strategies from callers.

## Decisions

### 1. Independent Vector Store Seam (`VectorStoreAdapter`)
- Define a strongly typed `VectorStoreAdapter` protocol in `app/rag/knowledge/base.py` with dual sync and native async methods:
  - `add_documents(documents: list[Document], ids: list[str] | None = None) -> list[str]`
  - `aadd_documents(documents: list[Document], ids: list[str] | None = None) -> list[str]`
  - `similarity_search(query: str, k: int = 4) -> list[Document]`
  - `asimilarity_search(query: str, k: int = 4) -> list[Document]`
  - `as_retriever(search_kwargs: dict[str, Any] | None = None) -> Any`
- Implement two concrete adapters satisfying the seam:
  - `PineconeVectorStoreAdapter`: Encapsulates Pinecone Serverless connection, non-destructive dimension validation, and namespace management without blocking polling loops.
  - `InMemoryVectorStoreAdapter`: Zero-dependency, offline in-memory store computing exact cosine similarity, equipped with a deterministic offline embedding double when no embedding API key is supplied.
- Provide an injectable factory `get_knowledge_store(settings=None, store_type=None)` resolving the configured adapter singleton.

### 2. Deep Write-Path Pipeline (`DocumentIndexer`)
- Consolidate file loading, chunking strategy dispatch, embedding batching, and vector persistence into a deep `DocumentIndexer` module (`app/rag/knowledge/indexer.py`).
- Expose a minimal, high-leverage entrypoint:
  - `index_file(path: Path | str) -> IndexingResult`
  - `aindex_file(path: Path | str) -> IndexingResult`
  - `index_batch(paths: list[Path | str]) -> list[IndexingResult]`
  - `aindex_batch(paths: list[Path | str]) -> list[IndexingResult]`
  *(Note on batch return type: Returning `list[IndexingResult]` rather than a single aggregate result allows granular, per-file error tracking, status inspection, and chunk reporting when processing multi-document batches without halting execution on individual file parse errors).*
- Parameterize `DocumentIndexer` with swappable strategy protocols:
  - `DocumentParser`: Reads file formats into normalized `Document` objects (built-in adapters for PDF, Markdown, Plaintext, and DOCX).
  - `DocumentChunker`: Partitions documents into retrieval-optimized chunks (default `RecursiveCharacterChunker` driven by application settings).
- Generate deterministic SHA-256 chunk IDs (`f"{source}:{chunk_index}:{content_hash}"`) to guarantee idempotent upserts and eliminate duplicate vectors across repeated ingestion runs.

### 3. Decoupled Read Path & Tool Seam Integration
- The retrieval tool seam (`create_retriever_tool` in `app/rag/tools/retriever.py`) consumes the read interface of `VectorStoreAdapter` directly via `adapter.as_retriever()`.
- Removes reflection into `sys.modules["app.rag.engine"]` and decouples retrieval nodes from vendor-specific storage packages.

### 4. Backward Compatibility Shims
- `app/rag/vectorstore.py` and `app/services/ingestion.py` remain as thin facade modules re-exporting `get_retriever`, `add_documents`, `load_file`, and `chunk_documents`, delegating directly to `app/rag/knowledge/`.
- First-party callers (`app/api/routes.py`, `ingest_sample_kb.py`, `app/rag/tools/retriever.py`) are migrated to `app/rag/knowledge/`.

## Consequences
- **Locality**: Document parsing, chunking, and vector storage concentrate inside `app/rag/knowledge/`. Modifying chunk overlap or file loaders affects zero callers.
- **Leverage**: HTTP upload routes and bulk CLI ingestion scripts invoke the same 1-method entrypoint (`aindex_file` / `index_batch`) without coordinating batching or threadpools.
- **Testability**: Unit and integration tests can execute against `InMemoryVectorStoreAdapter` completely offline in milliseconds without Pinecone credentials or network mocking.
- **Swappability**: Alternative vector engines (Qdrant, PGVector, Chroma) can be plugged in by implementing `VectorStoreAdapter` without modifying workflow graphs or API endpoints.
