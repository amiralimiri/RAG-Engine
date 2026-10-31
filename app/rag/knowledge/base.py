"""Base protocols and data models for the knowledge store subsystem."""

from typing import Any, Protocol, runtime_checkable

from langchain_core.documents import Document
from pydantic import BaseModel, Field


@runtime_checkable
class VectorStoreAdapter(Protocol):
    """Protocol seam defining storage and similarity retrieval for knowledge documents."""

    def add_documents(self, documents: list[Document], ids: list[str] | None = None) -> list[str]:
        """Synchronously persist documents with optional deterministic IDs."""
        ...

    async def aadd_documents(self, documents: list[Document], ids: list[str] | None = None) -> list[str]:
        """Asynchronously persist documents with optional deterministic IDs."""
        ...

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        """Synchronously retrieve top-k documents matching the query."""
        ...

    async def asimilarity_search(self, query: str, k: int = 4) -> list[Document]:
        """Asynchronously retrieve top-k documents matching the query."""
        ...

    def as_retriever(self, search_kwargs: dict[str, Any] | None = None) -> Any:
        """Return a LangChain-compatible retriever object exposing invoke and ainvoke."""
        ...


class ConfigurationError(Exception):
    """Raised when knowledge store or vector index configuration is invalid."""


class IndexingResult(BaseModel):
    """Normalized outcome returned by DocumentIndexer operations."""

    source: str = Field(..., description="File path or source identifier")
    document_count: int = Field(default=1, description="Number of parsed parent documents")
    chunk_count: int = Field(default=0, description="Total chunks created")
    ids_created: list[str] = Field(default_factory=list, description="List of vector IDs persisted")
    success: bool = Field(default=True, description="Whether indexing succeeded")
    error: str | None = Field(default=None, description="Error message if indexing failed")
