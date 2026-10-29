# 04: Pinecone Vector Store Adapter & Non-Destructive Validation

**Context & Specifications:**
- Specification: `docs/spec.md` & `docs/specs/0009-knowledge-store-seam-and-document-indexing-pipeline-spec.md`
- ADR: `docs/adr/0009-knowledge-store-seam-and-document-indexing-pipeline.md`
- Domain Glossary: `GLOSSARY.md` (terms: `Knowledge Store`, `Vector Store Adapter`)

**What to build:**
Implement `PineconeVectorStoreAdapter` in `app/rag/knowledge/vectorstore.py` satisfying `VectorStoreAdapter`:
- Encapsulate connection management to Pinecone Serverless using `settings.pinecone_api_key`.
- Non-destructive index validation:
  - Check whether the configured index exists and has the expected dimension for the embedding model.
  - Eliminate the previous destructive index deletion and blocking polling sleep loops (`while not pc.has_index: time.sleep(1)`). If the index dimension mismatches, raise a descriptive `ConfigurationError` rather than dropping the index.
- Implement `add_documents`, `aadd_documents`, `similarity_search`, `asimilarity_search`, and `as_retriever` delegating cleanly to underlying `PineconeVectorStore`.
- Implement `get_knowledge_store(settings=None, store_type=None) -> VectorStoreAdapter`:
  - Resolves `store_type` from `settings.vector_store_type` (defaulting to `"pinecone"` in production, and `"memory"` when Pinecone API key is omitted or for testing).
  - Thread-safe singleton instantiation with lock.

**Blocked by:** 
- Ticket 01: VectorStoreAdapter Protocol & Offline In-Memory Double

**Status:** ready-for-agent

- [ ] Red: Add test in `tests/rag/test_knowledge_store.py` asserting `PineconeVectorStoreAdapter` conforms to `VectorStoreAdapter` protocol.
- [ ] Red: Add test mocking Pinecone client asserting dimension mismatch raises `ConfigurationError` without deleting the index.
- [ ] Red: Add test asserting `get_knowledge_store(store_type="memory")` returns `InMemoryVectorStoreAdapter`, and `store_type="pinecone"` returns `PineconeVectorStoreAdapter`.
- [ ] Green: Implement `PineconeVectorStoreAdapter` in `app/rag/knowledge/vectorstore.py`.
- [ ] Green: Implement thread-safe `get_knowledge_store` factory with override support.
- [ ] Refactor: Export `PineconeVectorStoreAdapter` and `get_knowledge_store` in `app/rag/knowledge/__init__.py`.
