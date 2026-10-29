"""Backward-compatibility facade for vector store operations.

Delegates directly to app.rag.knowledge components.
"""

from typing import Any
from langchain_core.documents import Document

from app.core.config import get_settings
from app.rag.knowledge.vectorstore import (
    EMBEDDING_DIMENSIONS,
    get_embedding_dimension,
    get_knowledge_store,
)


def get_vectorstore() -> Any:
    """Backward-compatible access to the active vector store or adapter."""
    store = get_knowledge_store()
    if hasattr(store, "_store") and getattr(store, "_store", None) is not None:
        return store._store
    return store


def get_retriever(search_kwargs: dict[str, Any] | None = None) -> Any:
    """Backward-compatible retriever factory delegating to get_knowledge_store().as_retriever()."""
    cfg = get_settings()
    kwargs = search_kwargs if search_kwargs is not None else {"k": cfg.top_k}
    return get_knowledge_store().as_retriever(search_kwargs=kwargs)


def add_documents(chunks: list[Document], ids: list[str] | None = None) -> list[str]:
    """Backward-compatible document addition delegating to get_knowledge_store().add_documents()."""
    return get_knowledge_store().add_documents(chunks, ids=ids)


def get_embeddings() -> Any:
    """Backward-compatible embeddings factory."""
    from langchain_openai import OpenAIEmbeddings

    cfg = get_settings()
    if not cfg.embed_api_key:
        raise RuntimeError("API_KEY is missing")
    return OpenAIEmbeddings(
        model=cfg.embed_model,
        base_url=cfg.embed_base_url,
        api_key=cfg.embed_api_key,
    )


def ensure_index() -> Any:
    """Backward-compatible index validation facade."""
    store = get_knowledge_store()
    if hasattr(store, "index"):
        return store.index
    return None


__all__ = [
    "EMBEDDING_DIMENSIONS",
    "get_embedding_dimension",
    "get_knowledge_store",
    "get_vectorstore",
    "get_retriever",
    "add_documents",
    "get_embeddings",
    "ensure_index",
]
