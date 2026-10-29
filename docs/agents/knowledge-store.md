# Knowledge Store & Document Indexing Seam

Guidelines and patterns established in ADR-0009 (Knowledge Store Seam and Unified Document Indexing Pipeline) for vector persistence, parsing strategies, idempotent chunking, and offline test doubles.

---

## 1. Core Principles

1. **CQRS Separation**:
   - **Write Path (`DocumentIndexer`)**: Deep pipeline coordinating parsing (`DocumentParser`), chunking (`DocumentChunker`), batching, and upserting behind a single entrypoint (`index_file`, `index_batch`, `aindex_file`, `aindex_batch`).
   - **Read Path (`VectorStoreAdapter`)**: Seam defining similarity search and retriever export (`add_documents`, `aadd_documents`, `similarity_search`, `asimilarity_search`, `as_retriever`).
   - Callers and workflow nodes never coordinate chunking or vectorstore clients directly.

2. **Strict Idempotency via Deterministic Chunk IDs**:
   - Chunk IDs must be deterministic SHA-256 hashes combining source filename, chunk index, and content:
     ```python
     content_hash = hashlib.sha256(chunk.page_content.encode("utf-8")).hexdigest()[:16]
     chunk_id = f"{Path(path).name}:{idx}:{content_hash}"
     ```
   - Re-indexing identical content overwrites vectors in place without duplicating records or degrading vector similarity density.

3. **100% Offline Testability**:
   - `InMemoryVectorStoreAdapter` provides exact cosine similarity search over in-memory vectors.
   - Paired with `OfflineDeterministicEmbeddings`, all indexing and retrieval unit tests execute in milliseconds without Pinecone credentials or OpenAI API keys.
   - In automated test runners (`pytest` in `sys.modules`), `get_knowledge_store()` automatically resolves to `"memory"` unless explicitly configured otherwise.

4. **Non-Destructive Cloud Validation**:
   - Vector store initialization must never destructively drop or delete existing cloud indexes on dimension mismatch.
   - Raise a descriptive `ConfigurationError` explaining dimension differences rather than issuing destructive client calls.

5. **Decoupled Tool Seams (No Module Reflection)**:
   - `create_retriever_tool()` resolves its default retriever from `get_knowledge_store().as_retriever()`.
   - Never reflect into `sys.modules["app.rag.engine"]` or inspect global module state.

---

## 2. Friction Points & Lessons Learned

### 2.1. Parameter Shadowing vs. Instance Configuration
- **Issue**: Introducing a method parameter like `def index_batch(paths, batch_size=10)` shadows the instance's configured `self.batch_size` attribute (which controls chunk upsert batching) and bifurcates the configuration model.
- **Solution**: Keep method signatures minimal (`index_batch(paths)`). Parameterize batch size during `DocumentIndexer` instantiation (`DocumentIndexer(store, batch_size=...)`).

### 2.2. Backward Compatibility Facades & Encapsulation
- **Issue**: Facade shims (`app/rag/vectorstore.py:get_vectorstore()`) tempting developers to inspect private adapter attributes (e.g., `store._store`).
- **Solution**: Return the adapter seam directly (`return get_knowledge_store()`). The adapter implements the necessary contracts (`add_documents`, `similarity_search`, `as_retriever`) so callers never need the raw vendor client.

### 2.3. Honoring Out-of-Scope Boundaries
- **Issue**: Attempting to appease a legacy unit test by modifying an out-of-scope upstream file (`app/rag/engine.py:270`) rather than updating the mock fixture or maintaining clean seam defaults.
- **Solution**: Always respect documented out-of-scope boundaries. If a legacy test relied on monkeypatching module globals before a seam existed, update the test to patch the seam (`patch("app.rag.knowledge.get_knowledge_store", ...)`).
