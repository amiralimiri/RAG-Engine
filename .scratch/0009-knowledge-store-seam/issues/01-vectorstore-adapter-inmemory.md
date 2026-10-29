# 01: VectorStoreAdapter Protocol & Offline In-Memory Double

**Context & Specifications:**
- Specification: `docs/spec.md` & `docs/specs/0009-knowledge-store-seam-and-document-indexing-pipeline-spec.md`
- ADR: `docs/adr/0009-knowledge-store-seam-and-document-indexing-pipeline.md`
- Domain Glossary: `GLOSSARY.md` (terms: `Knowledge Store`, `Vector Store Adapter`)

**What to build:**
Establish the independent vector store protocol seam (`VectorStoreAdapter`) in `app/rag/knowledge/base.py` and implement `InMemoryVectorStoreAdapter` in `app/rag/knowledge/vectorstore.py` with dual sync and native async execution:
- `add_documents(documents: list[Document], ids: list[str] | None = None) -> list[str]`
- `aadd_documents(documents: list[Document], ids: list[str] | None = None) -> list[str]`
- `similarity_search(query: str, k: int = 4) -> list[Document]`
- `asimilarity_search(query: str, k: int = 4) -> list[Document]`
- `as_retriever(search_kwargs: dict[str, Any] | None = None) -> Any` returning a retriever exposing both `invoke` and `ainvoke`.

Equip `InMemoryVectorStoreAdapter` with:
- Exact cosine similarity ranking over in-memory vectors.
- Support for injected embeddings (`OpenAIEmbeddings`) or a deterministic offline fallback embedding double when `embeddings=None`, enabling 100% offline testing without network I/O or API keys.
- Define `IndexingResult` model in `app/rag/knowledge/base.py`.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Red: Create `tests/rag/test_knowledge_store.py` asserting `InMemoryVectorStoreAdapter` satisfies `isinstance(adapter, VectorStoreAdapter)`.
- [ ] Red: Add test for synchronous `add_documents` and `similarity_search` verifying exact cosine similarity ranking and top-k filtering with deterministic offline embeddings.
- [ ] Red: Add test for asynchronous `aadd_documents` and `asimilarity_search` asserting identical results to sync methods.
- [ ] Red: Add test for `as_retriever()` asserting both synchronous `retriever.invoke(query)` and asynchronous `await retriever.ainvoke(query)` return top relevant documents.
- [ ] Green: Implement `VectorStoreAdapter` protocol and `IndexingResult` model in `app/rag/knowledge/base.py`.
- [ ] Green: Implement `InMemoryVectorStoreAdapter` and offline embedding double in `app/rag/knowledge/vectorstore.py`.
- [ ] Refactor: Export components cleanly in `app/rag/knowledge/__init__.py` and ensure full typing and test coverage.
