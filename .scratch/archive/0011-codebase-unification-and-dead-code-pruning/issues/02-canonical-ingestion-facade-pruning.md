# 02: Canonical Ingestion & Storage Facade Pruning

**What to build:** Establish `SUPPORTED_EXTENSIONS` directly in `app.rag.knowledge` as the single source of truth for file ingestion formats, delete backward-compatibility pass-through facades `app/services/ingestion.py` and `app/rag/vectorstore.py`, update `app/api/routes.py` to import from canonical seams, and delete `tests/test_indexing_regression.py`.

**Blocked by:** 01: Test Isolation Fixture Foundation

**Status:** closed

---

## Tasks

- [x] In `app/rag/knowledge/parsers.py`, define and export `SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md", ".docx"})`.
- [x] In `app/rag/knowledge/__init__.py`, export `SUPPORTED_EXTENSIONS`.
- [x] In `app/api/routes.py`:
  - Replace `from app.services.ingestion import SUPPORTED` with `from app.rag.knowledge import SUPPORTED_EXTENSIONS`.
  - Update `upload_file` validation to check `suffix not in SUPPORTED_EXTENSIONS`.
- [x] Delete legacy facade file `app/services/ingestion.py`.
- [x] Delete legacy facade file `app/rag/vectorstore.py`.
- [x] Delete legacy test file `tests/test_indexing_regression.py`.
- [x] Update `tests/api/test_routes_upload.py` and `tests/rag/test_parsers_chunkers.py` to assert against `SUPPORTED_EXTENSIONS` directly.
- [x] Verify no references to `app.services.ingestion` or `app.rag.vectorstore` remain across `app/` and `tests/`.

## Verification
- Run `uv run pytest tests/api/test_routes_upload.py tests/rag/test_parsers_chunkers.py tests/rag/test_document_indexer.py tests/rag/test_knowledge_store.py`.
