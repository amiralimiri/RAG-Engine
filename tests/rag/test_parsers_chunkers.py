"""Tests for pluggable document parsers and chunker strategies."""

from pathlib import Path
import pytest
from docx import Document as DocxDocument
from langchain_core.documents import Document
from pypdf import PdfWriter

from app.core.config import Settings
from app.rag.knowledge.chunkers import (
    DocumentChunker,
    RecursiveCharacterChunker,
)
from app.rag.knowledge.parsers import (
    CompositeDocumentParser,
    DocxParser,
    DocumentParser,
    PDFParser,
    SUPPORTED_EXTENSIONS,
    TextParser,
)


@pytest.fixture
def temp_docs_dir(tmp_path: Path) -> Path:
    """Fixture providing sample files of supported and unsupported formats."""
    # 1. Plain text file
    txt_file = tmp_path / "sample.txt"
    txt_file.write_text("This is a plain text document for parsing tests.", encoding="utf-8")

    # 2. Markdown file
    md_file = tmp_path / "sample.md"
    md_file.write_text("# Title\n\nThis is a markdown document with sections.", encoding="utf-8")

    # 3. DOCX file
    docx_file = tmp_path / "sample.docx"
    doc = DocxDocument()
    doc.add_paragraph("Paragraph 1: Testing DOCX file parsing.")
    doc.add_paragraph("Paragraph 2: Second paragraph with details.")
    doc.save(str(docx_file))

    # 4. PDF file
    pdf_file = tmp_path / "sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf_file, "wb") as f:
        writer.write(f)

    # 5. Unsupported file
    unsupported_file = tmp_path / "data.json"
    unsupported_file.write_text('{"key": "value"}', encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# DocumentParser Protocol & Concrete Parsers Tests
# ---------------------------------------------------------------------------


def test_package_exports():
    """Verify parsers, chunkers, and supported extensions are cleanly exposed by app.rag.knowledge."""
    import app.rag.knowledge as pkg

    assert hasattr(pkg, "DocumentParser")
    assert hasattr(pkg, "PDFParser")
    assert hasattr(pkg, "TextParser")
    assert hasattr(pkg, "DocxParser")
    assert hasattr(pkg, "CompositeDocumentParser")
    assert hasattr(pkg, "DocumentChunker")
    assert hasattr(pkg, "RecursiveCharacterChunker")
    assert hasattr(pkg, "SUPPORTED_EXTENSIONS")
    assert pkg.SUPPORTED_EXTENSIONS == SUPPORTED_EXTENSIONS


def test_supported_extensions_source_of_truth():
    """Verify SUPPORTED_EXTENSIONS is a frozenset containing canonical file formats."""
    assert isinstance(SUPPORTED_EXTENSIONS, frozenset)
    assert SUPPORTED_EXTENSIONS == frozenset({".pdf", ".txt", ".md", ".docx"})


def test_parsers_implement_protocol():
    """Verify all parsers implement the DocumentParser protocol."""
    assert isinstance(PDFParser(), DocumentParser)
    assert isinstance(TextParser(), DocumentParser)
    assert isinstance(DocxParser(), DocumentParser)
    assert isinstance(CompositeDocumentParser(), DocumentParser)


def test_parser_can_parse_suffix():
    """Verify can_parse correctly matches supported file extensions."""
    pdf_parser = PDFParser()
    assert pdf_parser.can_parse(".pdf")
    assert pdf_parser.can_parse("pdf")
    assert pdf_parser.can_parse(".PDF")
    assert not pdf_parser.can_parse(".txt")

    text_parser = TextParser()
    assert text_parser.can_parse(".txt")
    assert text_parser.can_parse("txt")
    assert text_parser.can_parse(".md")
    assert text_parser.can_parse("MD")
    assert not text_parser.can_parse(".pdf")

    docx_parser = DocxParser()
    assert docx_parser.can_parse(".docx")
    assert docx_parser.can_parse("docx")
    assert docx_parser.can_parse(".DOCX")
    assert not docx_parser.can_parse(".doc")

    composite = CompositeDocumentParser()
    for ext in SUPPORTED_EXTENSIONS:
        assert composite.can_parse(ext)
        assert composite.can_parse(ext.upper())
        assert composite.can_parse(ext.lstrip("."))
        assert composite.can_parse(ext.lstrip(".").upper())
    assert not composite.can_parse(".json")
    assert not composite.can_parse(".csv")


def test_composite_parser_sync(temp_docs_dir: Path):
    """Verify CompositeDocumentParser parses all supported file formats synchronously."""
    composite = CompositeDocumentParser()

    # Parse .txt
    txt_docs = composite.parse(temp_docs_dir / "sample.txt")
    assert len(txt_docs) >= 1
    assert "plain text document" in txt_docs[0].page_content
    assert "sample.txt" in txt_docs[0].metadata.get("source", "")

    # Parse .md
    md_docs = composite.parse(temp_docs_dir / "sample.md")
    assert len(md_docs) >= 1
    assert "markdown document" in md_docs[0].page_content
    assert "sample.md" in md_docs[0].metadata.get("source", "")

    # Parse .docx
    docx_docs = composite.parse(temp_docs_dir / "sample.docx")
    assert len(docx_docs) == 1
    assert "Paragraph 1: Testing DOCX file parsing." in docx_docs[0].page_content
    assert "Paragraph 2: Second paragraph with details." in docx_docs[0].page_content
    assert "sample.docx" in docx_docs[0].metadata.get("source", "")

    # Parse .pdf
    pdf_docs = composite.parse(temp_docs_dir / "sample.pdf")
    assert len(pdf_docs) >= 1
    assert "sample.pdf" in pdf_docs[0].metadata.get("source", "")


@pytest.mark.asyncio
async def test_composite_parser_async(temp_docs_dir: Path):
    """Verify CompositeDocumentParser parses all supported file formats asynchronously."""
    composite = CompositeDocumentParser()

    txt_docs = await composite.aparse(temp_docs_dir / "sample.txt")
    assert len(txt_docs) >= 1
    assert "plain text document" in txt_docs[0].page_content

    md_docs = await composite.aparse(temp_docs_dir / "sample.md")
    assert len(md_docs) >= 1
    assert "markdown document" in md_docs[0].page_content

    docx_docs = await composite.aparse(temp_docs_dir / "sample.docx")
    assert len(docx_docs) == 1
    assert "Testing DOCX file parsing" in docx_docs[0].page_content

    pdf_docs = await composite.aparse(temp_docs_dir / "sample.pdf")
    assert len(pdf_docs) >= 1


def test_unsupported_file_extension_raises_value_error(temp_docs_dir: Path):
    """Verify unsupported file types raise a clear ValueError synchronously."""
    composite = CompositeDocumentParser()
    unsupported_path = temp_docs_dir / "data.json"

    with pytest.raises(ValueError, match="Unsupported file type"):
        composite.parse(unsupported_path)


@pytest.mark.asyncio
async def test_unsupported_file_extension_raises_value_error_async(temp_docs_dir: Path):
    """Verify unsupported file types raise a clear ValueError asynchronously."""
    composite = CompositeDocumentParser()
    unsupported_path = temp_docs_dir / "data.json"

    with pytest.raises(ValueError, match="Unsupported file type"):
        await composite.aparse(unsupported_path)


def test_composite_parser_custom_registry():
    """Verify custom parser registry ordering and delegation."""
    class DummyParser:
        def can_parse(self, suffix: str) -> bool:
            return suffix.lower() in {".custom", "custom"}

        def parse(self, path: Path | str) -> list[Document]:
            return [Document(page_content="custom content", metadata={"source": str(path)})]

        async def aparse(self, path: Path | str) -> list[Document]:
            return self.parse(path)

    composite = CompositeDocumentParser(parsers=[DummyParser()])
    assert composite.can_parse(".custom")
    assert not composite.can_parse(".pdf")

    docs = composite.parse("test.custom")
    assert docs[0].page_content == "custom content"


# ---------------------------------------------------------------------------
# DocumentChunker Protocol & RecursiveCharacterChunker Tests
# ---------------------------------------------------------------------------


def test_chunker_implements_protocol():
    """Verify RecursiveCharacterChunker satisfies DocumentChunker protocol."""
    chunker = RecursiveCharacterChunker(chunk_size=500, chunk_overlap=50)
    assert isinstance(chunker, DocumentChunker)


def test_chunker_splits_large_documents_preserving_metadata():
    """Verify RecursiveCharacterChunker splits large documents and preserves source and start_index."""
    chunker = RecursiveCharacterChunker(chunk_size=100, chunk_overlap=20)

    # 350-character content
    paragraph = (
        "The quick brown fox jumps over the lazy dog repeatedly to fill up text. "
        "Enterprise IT support documentation requires comprehensive runbooks and guides. "
        "Deterministic chunking ensures idempotency and reproducible indexing pipelines. "
        "LangChain splitter divides long text across specified boundaries."
    )
    doc = Document(page_content=paragraph, metadata={"source": "kb/handbook.md", "author": "IT Ops"})

    chunks = chunker.chunk([doc])

    assert len(chunks) > 1
    for chunk in chunks:
        # Source metadata must be preserved
        assert chunk.metadata["source"] == "kb/handbook.md"
        assert chunk.metadata["author"] == "IT Ops"
        # start_index must be present
        assert "start_index" in chunk.metadata
        assert isinstance(chunk.metadata["start_index"], int)
        # Content length should respect chunk_size
        assert len(chunk.page_content) <= 120  # splitter might slightly exceed depending on boundary


def test_chunker_empty_input():
    """Verify chunker handles empty documents gracefully."""
    chunker = RecursiveCharacterChunker()
    assert chunker.chunk([]) == []


def test_chunker_uses_settings_defaults():
    """Verify RecursiveCharacterChunker reads chunk_size and chunk_overlap from Settings."""
    custom_settings = Settings(
        llm_api_key="sk-fake",
        embed_api_key="sk-fake",
        jev_api_key="sk-fake",
        tavily_api_key="tvly-fake",
        pinecone_api_key="pc-fake",
        admin_api_key="admin-fake",
        chunk_size=450,
        chunk_overlap=45,
    )

    chunker = RecursiveCharacterChunker(settings=custom_settings)
    assert chunker.chunk_size == 450
    assert chunker.chunk_overlap == 45
