"""Knowledge subsystem providing storage seams and document indexing."""

from app.rag.knowledge.base import (
    ConfigurationError,
    IndexingResult,
    VectorStoreAdapter,
)
from app.rag.knowledge.chunkers import (
    DocumentChunker,
    RecursiveCharacterChunker,
)
from app.rag.knowledge.indexer import (
    DocumentIndexer,
    get_document_indexer,
)
from app.rag.knowledge.parsers import (
    CompositeDocumentParser,
    DocxParser,
    DocumentParser,
    PDFParser,
    SUPPORTED_EXTENSIONS,
    TextParser,
)
from app.rag.knowledge.vectorstore import (
    EMBEDDING_DIMENSIONS,
    InMemoryVectorStoreAdapter,
    InMemoryVectorStoreRetriever,
    OfflineDeterministicEmbeddings,
    PineconeVectorStoreAdapter,
    get_embedding_dimension,
    get_knowledge_store,
    reset_knowledge_store,
)

__all__ = [
    "VectorStoreAdapter",
    "ConfigurationError",
    "IndexingResult",
    "InMemoryVectorStoreAdapter",
    "InMemoryVectorStoreRetriever",
    "OfflineDeterministicEmbeddings",
    "PineconeVectorStoreAdapter",
    "get_knowledge_store",
    "reset_knowledge_store",
    "get_embedding_dimension",
    "EMBEDDING_DIMENSIONS",
    "DocumentParser",
    "PDFParser",
    "TextParser",
    "DocxParser",
    "CompositeDocumentParser",
    "SUPPORTED_EXTENSIONS",
    "DocumentChunker",
    "RecursiveCharacterChunker",
    "DocumentIndexer",
    "get_document_indexer",
]
