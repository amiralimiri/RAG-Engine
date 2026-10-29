# 03: Deep DocumentIndexer Write Pipeline with Deterministic Upserts

**Context & Specifications:**
- Specification: `docs/spec.md` & `docs/specs/0009-knowledge-store-seam-and-document-indexing-pipeline-spec.md`
- ADR: `docs/adr/0009-knowledge-store-seam-and-document-indexing-pipeline.md`
- Domain Glossary: `GLOSSARY.md` (terms: `Document Indexer`, `Knowledge Store`, `Vector Store Adapter`)

**What to build:**
Implement the deep write-path indexing pipeline in `app/rag/knowledge/indexer.py`:
- `DocumentIndexer` class composing `VectorStoreAdapter`, `DocumentParser`, and `DocumentChunker`.
- Expose dual sync/async methods:
  - `index_file(path: Path | str) -> IndexingResult`
  - `aindex_file(path: Path | str) -> IndexingResult`
  - `index_batch(paths: list[Path | str], batch_size: int = 10) -> list[IndexingResult]`
  - `aindex_batch(paths: list[Path | str], batch_size: int = 10) -> list[IndexingResult]`
- Implement deterministic chunk ID generation:
  - Compute SHA-256 content hash: `content_hash = hashlib.sha256(chunk.page_content.encode("utf-8")).hexdigest()[:16]`
  - Format ID: `f"{Path(path).name}:{idx}:{content_hash}"`
  - Guarantee upsert idempotency: re-indexing the same file must overwrite identical chunks rather than creating duplicate vector entries.
- Provide a default factory `get_document_indexer(store=None, settings=None) -> DocumentIndexer`.

**Blocked by:** 
- Ticket 01: VectorStoreAdapter Protocol & Offline In-Memory Double
- Ticket 02: Pluggable Document Parsing and Chunking Strategy Protocols

**Status:** closed

- [x] Red: Create `tests/rag/test_document_indexer.py` asserting `index_file` and `aindex_file` successfully parse, chunk, and index a document into `InMemoryVectorStoreAdapter`.
- [x] Red: Add test asserting `IndexingResult` returns accurate document counts, chunk counts, and created vector IDs.
- [x] Red: Add idempotency test asserting re-indexing the same file produces identical chunk IDs and does not increase the total vector count in the store.
- [x] Red: Add test asserting `index_batch` and `aindex_batch` process multiple files in batched slices.
- [x] Red: Add test asserting unhandled parsing errors return an `IndexingResult` with `success=False` and the captured error message without crashing the batch.
- [x] Green: Implement `DocumentIndexer` and `get_document_indexer` in `app/rag/knowledge/indexer.py`.
- [x] Refactor: Export `DocumentIndexer` in `app/rag/knowledge/__init__.py`.
