"""Knowledge subsystem providing storage seams and document indexing."""

from app.rag.knowledge.base import IndexingResult, VectorStoreAdapter
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
]

