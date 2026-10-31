"""Document chunking strategy protocols and text splitters."""

from typing import Any, Protocol, runtime_checkable

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import Settings, get_settings


@runtime_checkable
class DocumentChunker(Protocol):
    """Protocol seam defining document partitioning into retrieval-optimized chunks."""

    def chunk(self, documents: list[Document]) -> list[Document]:
        """Partition documents into retrieval chunks."""
        ...


class RecursiveCharacterChunker:
    """Default chunker wrapping LangChain RecursiveCharacterTextSplitter.

    Reads chunk_size and chunk_overlap from application settings unless explicitly overridden.
    """

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        add_start_index: bool = True,
        settings: Settings | None = None,
        **kwargs: Any,
    ) -> None:
        cfg = settings or get_settings()
        self.chunk_size: int = chunk_size if chunk_size is not None else cfg.chunk_size
        self.chunk_overlap: int = chunk_overlap if chunk_overlap is not None else cfg.chunk_overlap
        self.add_start_index: bool = add_start_index

        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            add_start_index=self.add_start_index,
            **kwargs,
        )

    def chunk(self, documents: list[Document]) -> list[Document]:
        """Split documents into chunks preserving metadata and adding start_index."""
        if not documents:
            return []
        return self._splitter.split_documents(list(documents))
