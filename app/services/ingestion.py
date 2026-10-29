"""Backward-compatibility facade for document loading and chunking.

Delegates directly to app.rag.knowledge parsing and chunking components.
"""

from collections.abc import Iterable
from pathlib import Path
from langchain_core.documents import Document

from app.rag.knowledge.chunkers import RecursiveCharacterChunker
from app.rag.knowledge.parsers import CompositeDocumentParser

SUPPORTED = frozenset({".pdf", ".txt", ".md", ".docx"})


def load_file(path: Path | str) -> list[Document]:
    """Parse a file into Document objects, delegating to CompositeDocumentParser."""
    parser = CompositeDocumentParser()
    return parser.parse(Path(path))


def chunk_documents(docs: Iterable[Document]) -> list[Document]:
    """Split documents into chunks, delegating to RecursiveCharacterChunker."""
    chunker = RecursiveCharacterChunker()
    return chunker.chunk(list(docs))


__all__ = [
    "SUPPORTED",
    "load_file",
    "chunk_documents",
]
