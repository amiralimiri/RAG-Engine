# 05: Caller Modernization, Tool Seam Integration & Backward Compatibility Shims

**Context & Specifications:**
- Specification: `docs/spec.md` & `docs/specs/0009-knowledge-store-seam-and-document-indexing-pipeline-spec.md`
- ADR: `docs/adr/0009-knowledge-store-seam-and-document-indexing-pipeline.md`
- Domain Glossary: `GLOSSARY.md` (terms: `Document Indexer`, `Knowledge Store`, `Tool Abstraction Layer`)

**What to build:**
Migrate all first-party callers and tools to the new `app/rag/knowledge/` subsystem and preserve backward-compatibility facades:
1. Modernize HTTP upload in `app/api/routes.py`:
   - Replace manual 15-line `load_file` / `chunk_documents` / `run_in_threadpool` / `add_documents` orchestration with a single call:
     ```python
     indexer = get_document_indexer()
     result = await indexer.aindex_file(dest)
     ```
2. Modernize bulk sample KB script `ingest_sample_kb.py`:
   - Replace manual loop, progress bar batching, and `add_documents` calls with:
     ```python
     indexer = get_document_indexer()
     results = indexer.index_batch(files)
     ```
3. Modernize retrieval tool in `app/rag/tools/retriever.py`:
   - Update `_get_active_retriever()` to consume `get_knowledge_store().as_retriever()` directly when no explicit retriever is injected.
   - Delete all reflection hacks inspecting `sys.modules.get("app.rag.engine")`.
4. Backward compatibility shims:
   - Convert `app/rag/vectorstore.py` and `app/services/ingestion.py` into thin facades re-exporting `get_retriever`, `add_documents`, `get_vectorstore`, `load_file`, `chunk_documents` delegating to `app/rag/knowledge/`.
   - Verify that all existing 376 tests and new test suites pass with zero regressions.

**Blocked by:** 
- Ticket 03: Deep DocumentIndexer Write Pipeline with Deterministic Upserts
- Ticket 04: Pinecone Vector Store Adapter & Non-Destructive Validation

**Status:** closed

- [x] Red: Add test in `tests/api/test_routes_upload.py` asserting `POST /api/upload` uses `DocumentIndexer` and returns HTTP 200 with index results against `InMemoryVectorStoreAdapter`.
- [x] Red: Add regression test asserting legacy imports from `app.rag.vectorstore` and `app.services.ingestion` execute without error.
- [x] Red: Add test in `tests/rag/test_tools.py` verifying `create_retriever_tool` works without `sys.modules["app.rag.engine"]` reflection.
- [x] Green: Refactor `app/api/routes.py:upload_file` to use `indexer.aindex_file`.
- [x] Green: Refactor `ingest_sample_kb.py` to use `indexer.index_batch`.
- [x] Green: Refactor `app/rag/tools/retriever.py` to obtain its retriever from `get_knowledge_store()`.
- [x] Green: Refactor `app/rag/vectorstore.py` and `app/services/ingestion.py` as backward-compatible delegation shims.
- [x] Refactor: Run full regression test suite (`uv run pytest`) asserting all 376+ tests pass cleanly.
