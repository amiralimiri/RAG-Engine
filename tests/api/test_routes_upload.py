"""Tests for modernized document upload and ingestion route in app/api/routes.py."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from app.core.config import get_settings
from app.main import app
from app.rag.knowledge import (
    InMemoryVectorStoreAdapter,
    IndexingResult,
    get_knowledge_store,
    reset_knowledge_store,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_memory_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_knowledge_store()
    settings = get_settings()
    monkeypatch.setattr(settings, "vector_store_type", "memory")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    store = get_knowledge_store()
    yield store
    reset_knowledge_store()


def test_upload_success_with_in_memory_store(client: TestClient, setup_memory_store: InMemoryVectorStoreAdapter):
    """POST /api/upload indexes file via DocumentIndexer and returns 200 with index results."""
    settings = get_settings()
    admin_key = settings.admin_api_key.get_secret_value()

    file_content = b"Antigravity engineering handbook: deploy deep modules and clean seams."
    files = {"file": ("handbook.txt", file_content, "text/plain")}
    headers = {"X-Admin-Key": admin_key}

    response = client.post("/api/upload", files=files, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Document indexed"
    assert data["file"] == "handbook.txt"
    assert data["chunks"] >= 1
    assert data["ids_created"] >= 1

    # Verify that the InMemoryVectorStoreAdapter actually persisted the document
    results = setup_memory_store.similarity_search("Antigravity engineering", k=1)
    assert len(results) >= 1
    assert "Antigravity engineering handbook" in results[0].page_content


def test_ingest_alias_backward_compat(client: TestClient, setup_memory_store: InMemoryVectorStoreAdapter):
    """POST /api/ingest continues to work as a backward-compatible alias to upload_file."""
    settings = get_settings()
    admin_key = settings.admin_api_key.get_secret_value()

    file_content = b"# Policy Document\nAll employees must use secure authentication protocols."
    files = {"file": ("policy.md", file_content, "text/markdown")}
    headers = {"X-Admin-Key": admin_key}

    response = client.post("/api/ingest", files=files, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Document indexed"
    assert data["file"] == "policy.md"
    assert data["chunks"] >= 1
    assert data["ids_created"] >= 1


def test_upload_unauthorized(client: TestClient):
    """POST /api/upload without valid X-Admin-Key returns 401."""
    files = {"file": ("unauthorized.txt", b"secret data", "text/plain")}
    headers = {"X-Admin-Key": "wrong-key"}

    response = client.post("/api/upload", files=files, headers=headers)
    assert response.status_code == 401
    assert "Invalid admin key" in response.json()["detail"]


def test_upload_unsupported_extension(client: TestClient):
    """POST /api/upload with unsupported file extension returns 400."""
    settings = get_settings()
    admin_key = settings.admin_api_key.get_secret_value()

    files = {"file": ("test.exe", b"binary content", "application/octet-stream")}
    headers = {"X-Admin-Key": admin_key}

    response = client.post("/api/upload", files=files, headers=headers)
    assert response.status_code == 400
    assert "Supported:" in response.json()["detail"]


def test_upload_indexer_failure_returns_500(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """POST /api/upload returns 500 when DocumentIndexer fails."""
    settings = get_settings()
    admin_key = settings.admin_api_key.get_secret_value()

    mock_indexer = AsyncMock()
    mock_indexer.aindex_file.return_value = IndexingResult(
        source="broken.txt",
        document_count=0,
        chunk_count=0,
        ids_created=[],
        success=False,
        error="Parser crashed unexpectedly",
    )

    with patch("app.api.routes.get_document_indexer", return_value=mock_indexer):
        files = {"file": ("broken.txt", b"corrupted data", "text/plain")}
        headers = {"X-Admin-Key": admin_key}

        response = client.post("/api/upload", files=files, headers=headers)
        assert response.status_code == 500
        assert "Ingestion failed: Parser crashed unexpectedly" in response.json()["detail"]
