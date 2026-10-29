# 02: Pluggable Document Parsing and Chunking Strategy Protocols

**Context & Specifications:**
- Specification: `docs/spec.md` & `docs/specs/0009-knowledge-store-seam-and-document-indexing-pipeline-spec.md`
- ADR: `docs/adr/0009-knowledge-store-seam-and-document-indexing-pipeline.md`
- Domain Glossary: `GLOSSARY.md` (terms: `Document Parser`, `Document Chunker`)

**What to build:**
Define pluggable strategy protocols for document parsing and text chunking in `app/rag/knowledge/parsers.py` and `app/rag/knowledge/chunkers.py`:
- `DocumentParser`: Protocol declaring `can_parse(suffix: str) -> bool`, `parse(path: Path | str) -> list[Document]`, and `aparse(path: Path | str) -> list[Document]`.
  - Built-in adapters: `PDFParser` (PDF files via PyPDFLoader), `TextParser` (Plaintext `.txt` and Markdown `.md` via TextLoader), `DocxParser` (DOCX files via python-docx).
  - Composite `CompositeDocumentParser`: Ordered registry matching file extensions and delegating parsing.
- `DocumentChunker`: Protocol declaring `chunk(documents: list[Document]) -> list[Document]`.
  - Default `RecursiveCharacterChunker`: Parameterized by `chunk_size` and `chunk_overlap` from application settings (`app/core/config.py`).

**Blocked by:** None (can start immediately in parallel with Ticket 01)

**Status:** closed

- [x] Red: Create `tests/rag/test_parsers_chunkers.py` asserting `CompositeDocumentParser` correctly identifies and parses `.pdf`, `.md`, `.txt`, and `.docx` files both synchronously and asynchronously.
- [x] Red: Add test asserting unsupported file extensions raise a clear `ValueError`.
- [x] Red: Add test asserting `RecursiveCharacterChunker` splits large documents into chunks preserving metadata (`source`, `start_index`) with configured overlap.
- [x] Green: Implement `DocumentParser` protocol and concrete format parsers in `app/rag/knowledge/parsers.py`.
- [x] Green: Implement `DocumentChunker` protocol and `RecursiveCharacterChunker` in `app/rag/knowledge/chunkers.py`.
- [x] Refactor: Export parsers and chunkers cleanly in `app/rag/knowledge/__init__.py`.
