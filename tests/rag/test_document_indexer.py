"""Tests for the deep DocumentIndexer write pipeline with deterministic upserts."""

import hashlib
from pathlib import Path
import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.core.config import Settings
from app.rag.knowledge.base import IndexingResult
from app.rag.knowledge.chunkers import RecursiveCharacterChunker
from app.rag.knowledge.indexer import DocumentIndexer, get_document_indexer
from app.rag.knowledge.parsers import CompositeDocumentParser
from app.rag.knowledge.vectorstore import InMemoryVectorStoreAdapter


@pytest.fixture
def sample_docs(tmp_path: Path) -> dict[str, Path]:
    """Create a set of sample documents for testing indexing pipeline."""
    txt_path = tmp_path / "guide.txt"
    txt_path.write_text(
        "Network Setup Guide.\nTo configure VPN, install the company client and enter server details.\n"
        "Ensure MFA is enabled on your portal account before connecting.",
        encoding="utf-8",
    )

    md_path = tmp_path / "faq.md"
    md_path.write_text(
        "# IT Helpdesk FAQ\n\n## Password Reset\nVisit portal.company.com/reset to reset your password.\n"
        "Passwords must be at least 16 characters long.",
        encoding="utf-8",
    )

    docx_path = tmp_path / "policy.docx"
    doc = DocxDocument()
    doc.add_paragraph("Remote Work Policy: Employees must connect via approved VPN tunnels.")
    doc.save(str(docx_path))

    pdf_path = tmp_path / "handbook.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    return {
        "txt": txt_path,
        "md": md_path,
        "docx": docx_path,
        "pdf": pdf_path,
    }


def test_index_file_sync_and_async(sample_docs: dict[str, Path]):
    """Assert index_file and aindex_file successfully parse, chunk, and index into InMemoryVectorStoreAdapter."""
    store_sync = InMemoryVectorStoreAdapter()
    indexer_sync = DocumentIndexer(store=store_sync)

    result_sync = indexer_sync.index_file(sample_docs["txt"])
    assert result_sync.success is True
    assert result_sync.document_count >= 1
    assert result_sync.chunk_count >= 1
    assert len(store_sync._records) == result_sync.chunk_count

    # Similarity search should find indexed content
    matches = store_sync.similarity_search("VPN client setup", k=1)
    assert len(matches) == 1
    assert "VPN" in matches[0].page_content


@pytest.mark.asyncio
async def test_aindex_file_parity(sample_docs: dict[str, Path]):
    """Assert aindex_file has behavioral parity with sync index_file."""
    store_async = InMemoryVectorStoreAdapter()
    indexer_async = DocumentIndexer(store=store_async)

    result_async = await indexer_async.aindex_file(sample_docs["txt"])
    assert result_async.success is True
    assert result_async.document_count >= 1
    assert result_async.chunk_count >= 1
    assert len(store_async._records) == result_async.chunk_count

    matches = await store_async.asimilarity_search("VPN client setup", k=1)
    assert len(matches) == 1
    assert "VPN" in matches[0].page_content


def test_indexing_result_accurate_counts_and_ids(sample_docs: dict[str, Path]):
    """Assert IndexingResult returns accurate document counts, chunk counts, and formatted vector IDs."""
    store = InMemoryVectorStoreAdapter()
    chunker = RecursiveCharacterChunker(chunk_size=50, chunk_overlap=10)
    indexer = DocumentIndexer(store=store, chunker=chunker)

    txt_file = sample_docs["txt"]
    result = indexer.index_file(txt_file)

    assert isinstance(result, IndexingResult)
    assert result.source == str(txt_file)
    assert result.document_count == 1
    assert result.chunk_count > 1
    assert len(result.ids_created) == result.chunk_count
    assert result.success is True
    assert result.error is None

    # Verify ID formatting: f"{Path(path).name}:{idx}:{content_hash}"
    for idx, chunk_id in enumerate(result.ids_created):
        parts = chunk_id.split(":")
        assert len(parts) == 3
        assert parts[0] == txt_file.name
        assert parts[1] == str(idx)
        assert len(parts[2]) == 16  # 16-char sha256 prefix


def test_upsert_idempotency_sync_and_async(sample_docs: dict[str, Path]):
    """Assert re-indexing the same file produces identical chunk IDs and does not increase store vector count."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store)
    md_file = sample_docs["md"]

    # First indexing run
    res1 = indexer.index_file(md_file)
    count_after_first = len(store._records)
    assert count_after_first > 0
    assert len(res1.ids_created) == count_after_first

    # Second indexing run (re-index same file)
    res2 = indexer.index_file(md_file)
    count_after_second = len(store._records)

    # Identical IDs generated and total record count remains constant
    assert res1.ids_created == res2.ids_created
    assert count_after_second == count_after_first


@pytest.mark.asyncio
async def test_upsert_idempotency_async(sample_docs: dict[str, Path]):
    """Assert aindex_file also guarantees idempotency and stable IDs."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store)
    md_file = sample_docs["md"]

    res1 = await indexer.aindex_file(md_file)
    count_first = len(store._records)

    res2 = await indexer.aindex_file(md_file)
    count_second = len(store._records)

    assert res1.ids_created == res2.ids_created
    assert count_first == count_second


def test_index_batch_processes_in_batched_slices(sample_docs: dict[str, Path]):
    """Assert index_batch processes multiple files in batched slices."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store, batch_size=2)

    paths = [sample_docs["txt"], sample_docs["md"], sample_docs["docx"], sample_docs["pdf"]]
    results = indexer.index_batch(paths)

    assert len(results) == len(paths)
    for res in results:
        assert res.success is True
        assert res.error is None
        assert res.chunk_count >= 0

    assert len(store._records) > 0


@pytest.mark.asyncio
async def test_aindex_batch_processes_in_batched_slices(sample_docs: dict[str, Path]):
    """Assert aindex_batch processes multiple files concurrently in slices."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store, batch_size=2)

    paths = [sample_docs["txt"], sample_docs["md"], sample_docs["docx"], sample_docs["pdf"]]
    results = await indexer.aindex_batch(paths)

    assert len(results) == len(paths)
    for res in results:
        assert res.success is True
        assert res.error is None

    assert len(store._records) > 0


def test_batch_empty_list():
    """Assert index_batch and aindex_batch handle empty lists gracefully."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store)

    assert indexer.index_batch([]) == []


@pytest.mark.asyncio
async def test_aindex_batch_empty_list():
    """Assert aindex_batch handles empty lists gracefully."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store)

    assert await indexer.aindex_batch([]) == []


def test_unhandled_parsing_errors_captured_without_crashing_batch(
    sample_docs: dict[str, Path], tmp_path: Path
):
    """Assert unhandled parsing errors return success=False and captured error message without crashing batch."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store, batch_size=2)

    unsupported_file = tmp_path / "corrupted.unsupported"
    unsupported_file.write_text("Unsupported content format", encoding="utf-8")

    paths = [
        sample_docs["txt"],
        unsupported_file,
        sample_docs["md"],
    ]

    results = indexer.index_batch(paths)
    assert len(results) == 3

    # First file succeeded
    assert results[0].success is True
    assert results[0].source == str(sample_docs["txt"])
    assert results[0].error is None

    # Second file failed gracefully
    assert results[1].success is False
    assert results[1].source == str(unsupported_file)
    assert results[1].error is not None
    assert "Unsupported file type" in results[1].error
    assert results[1].chunk_count == 0
    assert results[1].ids_created == []

    # Third file still succeeded despite second file failing
    assert results[2].success is True
    assert results[2].source == str(sample_docs["md"])
    assert results[2].error is None


@pytest.mark.asyncio
async def test_aindex_batch_error_resilience(
    sample_docs: dict[str, Path], tmp_path: Path
):
    """Assert aindex_batch handles individual file errors gracefully in concurrent slices."""
    store = InMemoryVectorStoreAdapter()
    indexer = DocumentIndexer(store=store, batch_size=2)

    unsupported_file = tmp_path / "corrupted.unsupported"
    unsupported_file.write_text("Unsupported content format", encoding="utf-8")

    paths = [
        sample_docs["txt"],
        unsupported_file,
        sample_docs["md"],
    ]

    results = await indexer.aindex_batch(paths)
    assert len(results) == 3
    assert results[0].success is True
    assert results[1].success is False
    assert "Unsupported file type" in results[1].error
    assert results[2].success is True


def test_package_exports():
    """Assert DocumentIndexer and factory are exported by app.rag.knowledge."""
    import app.rag.knowledge as pkg

    assert hasattr(pkg, "DocumentIndexer")
    assert hasattr(pkg, "get_document_indexer")


def test_get_document_indexer_factory():
    """Assert get_document_indexer factory returns a DocumentIndexer configured properly."""
    custom_store = InMemoryVectorStoreAdapter()
    indexer = get_document_indexer(store=custom_store)

    assert isinstance(indexer, DocumentIndexer)
    assert indexer.store is custom_store
    assert isinstance(indexer.parser, CompositeDocumentParser)
    assert isinstance(indexer.chunker, RecursiveCharacterChunker)

    # Test default store fallback
    default_indexer = get_document_indexer()
    assert isinstance(default_indexer, DocumentIndexer)
    assert default_indexer.store is not None
