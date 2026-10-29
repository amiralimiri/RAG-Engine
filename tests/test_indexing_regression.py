"""Regression tests verifying backward-compatibility facades for app.rag.vectorstore and app.services.ingestion."""

from pathlib import Path
import pytest
from langchain_core.documents import Document

from app.rag.knowledge import (
    InMemoryVectorStoreAdapter,
    reset_knowledge_store,
)


@pytest.fixture(autouse=True)
def setup_knowledge_store(monkeypatch: pytest.MonkeyPatch):
    reset_knowledge_store()
    mem_store = InMemoryVectorStoreAdapter()
    monkeypatch.setattr("app.rag.knowledge.vectorstore.get_knowledge_store", lambda **kw: mem_store)
    yield mem_store
    reset_knowledge_store()


class TestLegacyIngestionFacade:
    """Validate app.services.ingestion backward-compatible exports."""

    def test_legacy_imports_from_services_ingestion(self):
        from app.services.ingestion import SUPPORTED, chunk_documents, load_file

        assert isinstance(SUPPORTED, (set, frozenset))
        assert {".pdf", ".txt", ".md", ".docx"}.issubset(SUPPORTED)
        assert callable(load_file)
        assert callable(chunk_documents)

    def test_load_file_with_txt_and_md(self, tmp_path: Path):
        from app.services.ingestion import load_file

        txt_file = tmp_path / "sample.txt"
        txt_file.write_text("Hello from plain text.", encoding="utf-8")

        docs = load_file(txt_file)
        assert len(docs) == 1
        assert docs[0].page_content == "Hello from plain text."

        md_file = tmp_path / "sample.md"
        md_file.write_text("# Markdown Title\nContent here.", encoding="utf-8")

        md_docs = load_file(md_file)
        assert len(md_docs) == 1
        assert "Markdown Title" in md_docs[0].page_content

    def test_load_file_unsupported_type(self, tmp_path: Path):
        from app.services.ingestion import load_file

        bad_file = tmp_path / "sample.unsupported"
        bad_file.write_text("dummy", encoding="utf-8")

        with pytest.raises(ValueError, match="Unsupported file type"):
            load_file(bad_file)

    def test_chunk_documents(self):
        from app.services.ingestion import chunk_documents

        long_text = "Sentence number one. " * 100
        docs = [Document(page_content=long_text, metadata={"source": "test.txt"})]

        chunks = chunk_documents(docs)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.metadata.get("source") == "test.txt"


class TestLegacyVectorstoreFacade:
    """Validate app.rag.vectorstore backward-compatible exports."""

    def test_legacy_imports_from_rag_vectorstore(self):
        from app.rag.vectorstore import (
            EMBEDDING_DIMENSIONS,
            add_documents,
            get_embedding_dimension,
            get_retriever,
            get_vectorstore,
        )

        assert isinstance(EMBEDDING_DIMENSIONS, dict)
        assert "text-embedding-3-small" in EMBEDDING_DIMENSIONS
        assert callable(get_embedding_dimension)
        assert callable(add_documents)
        assert callable(get_retriever)
        assert callable(get_vectorstore)

    def test_get_embedding_dimension(self):
        from app.rag.vectorstore import get_embedding_dimension

        assert get_embedding_dimension("text-embedding-3-small") == 1536
        assert get_embedding_dimension("text-embedding-3-large") == 3072
        assert get_embedding_dimension("all-minilm-l6-v2") == 384

    def test_add_documents_and_get_retriever(self, setup_knowledge_store: InMemoryVectorStoreAdapter):
        from app.rag.vectorstore import add_documents, get_retriever, get_vectorstore

        vs = get_vectorstore()
        assert vs is not None

        docs = [
            Document(page_content="Policy: Always sanitize user input.", metadata={"source": "sec.md"}),
            Document(page_content="Guide: How to set up local dev environment.", metadata={"source": "dev.md"}),
        ]

        ids = add_documents(docs)
        assert len(ids) == 2

        retriever = get_retriever()
        assert retriever is not None

        retrieved = retriever.invoke("sanitize input")
        assert len(retrieved) >= 1
        assert "sanitize user input" in retrieved[0].page_content

    def test_ensure_index_and_get_embeddings_shims(self, monkeypatch: pytest.MonkeyPatch):
        from app.core.config import get_settings
        from app.rag.vectorstore import ensure_index, get_embeddings

        # ensure_index returns index or None without throwing
        idx = ensure_index()
        assert idx is None or idx is not None

        # get_embeddings raises RuntimeError when API key is missing
        cfg = get_settings()
        monkeypatch.setattr(cfg, "embed_api_key", None)
        with pytest.raises(RuntimeError, match="API_KEY is missing"):
            get_embeddings()


class TestSampleKBIngestionScript:
    """Validate ingest_sample_kb.py modernization with DocumentIndexer."""

    def test_ingest_sample_kb_batch(self, tmp_path: Path):
        from ingest_sample_kb import ingest_sample_kb

        # Create two sample files
        doc1 = tmp_path / "guide.md"
        doc1.write_text("# Developer Guide\nFollow testing guidelines before submitting PRs.", encoding="utf-8")
        doc2 = tmp_path / "faq.txt"
        doc2.write_text("Q: Where is staging?\nA: Staging is deployed on AWS.", encoding="utf-8")

        results = ingest_sample_kb(files=[doc1, doc2])
        assert len(results) == 2
        assert all(r.success for r in results)
        assert sum(r.chunk_count for r in results) >= 2
        assert sum(len(r.ids_created) for r in results) >= 2

