"""Knowledge subsystem providing storage seams and document indexing."""

from app.rag.knowledge.base import IndexingResult, VectorStoreAdapter
from app.rag.knowledge.chunkers import (
    DocumentChunker,
    RecursiveCharacterChunker,
)
from app.rag.knowledge.parsers import (
    CompositeDocumentParser,
    DocxParser,
    DocumentParser,
    PDFParser,
    TextParser,
)
from app.rag.knowledge.vectorstore import (
    InMemoryVectorStoreAdapter,
    InMemoryVectorStoreRetriever,
    OfflineDeterministicEmbeddings,
)

__all__ = [
    "VectorStoreAdapter",
    "IndexingResult",
    "InMemoryVectorStoreAdapter",
    "InMemoryVectorStoreRetriever",
    "OfflineDeterministicEmbeddings",
    "DocumentParser",
    "PDFParser",
    "TextParser",
    "DocxParser",
    "CompositeDocumentParser",
    "DocumentChunker",
    "RecursiveCharacterChunker",
]
