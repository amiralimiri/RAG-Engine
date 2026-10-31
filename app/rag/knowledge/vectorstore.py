"""Vector store adapters, cloud Pinecone integration, and offline doubles."""

import hashlib
import inspect
import math
import os
import re
import sys
import threading
import uuid
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from pydantic import Field

from app.core.config import Settings, get_settings
from app.rag.knowledge.base import ConfigurationError, VectorStoreAdapter

EMBEDDING_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "all-minilm-l6-v2": 384,
}


def get_embedding_dimension(model_name: str | None = None, settings: Settings | None = None) -> int:
    """Resolve expected vector dimension for a given embedding model name or settings."""
    if model_name is not None:
        name = model_name.strip()
    else:
        cfg = settings or get_settings()
        name = (getattr(cfg, "embed_model", None) or "").strip()

    if not name:
        raise ConfigurationError("Embedding model is not configured.")

    normalized = name.lower()
    if normalized in EMBEDDING_DIMENSIONS:
        return EMBEDDING_DIMENSIONS[normalized]

    for key, dim in EMBEDDING_DIMENSIONS.items():
        if key in normalized:
            return dim

    raise ConfigurationError(
        f"Unsupported embedding model '{name}' for Pinecone. "
        "Add the matching dimension to EMBEDDING_DIMENSIONS."
    )


def _cosine_similarity(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v))
    norm_u = sum(a * a for a in u) ** 0.5
    norm_v = sum(b * b for b in v) ** 0.5
    if norm_u == 0.0 or norm_v == 0.0:
        return 0.0
    return dot / (norm_u * norm_v)


class OfflineDeterministicEmbeddings(Embeddings):

    """Deterministic offline hash embedding double for network-free local testing."""

    def __init__(self, dimension: int = 128) -> None:
        self.dimension = dimension

    def _embed(self, text: str) -> list[float]:
        tokens = re.findall(r"\w+", text.lower())
        vec = [0.0] * self.dimension
        if not tokens:
            return vec
        for token in tokens:
            idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dimension
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


class InMemoryVectorStoreAdapter:
    """In-memory implementation of VectorStoreAdapter with exact cosine similarity."""

    def __init__(self, embeddings: Embeddings | None = None) -> None:
        self.embeddings = embeddings if embeddings is not None else OfflineDeterministicEmbeddings()
        self._records: dict[str, tuple[Document, list[float]]] = {}

    def _validate_and_assign_ids(self, documents: list[Document], ids: list[str] | None) -> list[str]:
        if ids is not None and len(ids) != len(documents):
            raise ValueError(f"Number of ids ({len(ids)}) does not match number of documents ({len(documents)})")
        return ids if ids is not None else [str(uuid.uuid4()) for _ in documents]

    def _rank_documents(self, query_vec: list[float], k: int) -> list[Document]:
        if not self._records:
            return []
        scored: list[tuple[float, Document]] = []
        for doc, doc_vec in self._records.values():
            sim = _cosine_similarity(query_vec, doc_vec)
            scored.append((sim, doc))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in scored[:k]]

    def add_documents(self, documents: list[Document], ids: list[str] | None = None) -> list[str]:
        if not documents:
            return []
        assigned_ids = self._validate_and_assign_ids(documents, ids)
        texts = [doc.page_content for doc in documents]
        vectors = self.embeddings.embed_documents(texts)

        for doc_id, doc, vec in zip(assigned_ids, documents, vectors):
            self._records[doc_id] = (doc, vec)

        return assigned_ids

    async def aadd_documents(self, documents: list[Document], ids: list[str] | None = None) -> list[str]:
        if not documents:
            return []
        assigned_ids = self._validate_and_assign_ids(documents, ids)
        texts = [doc.page_content for doc in documents]
        if hasattr(self.embeddings, "aembed_documents"):
            res = self.embeddings.aembed_documents(texts)
            vectors = await res if inspect.isawaitable(res) else res
        else:
            vectors = self.embeddings.embed_documents(texts)

        for doc_id, doc, vec in zip(assigned_ids, documents, vectors):
            self._records[doc_id] = (doc, vec)

        return assigned_ids

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        if not self._records:
            return []
        query_vec = self.embeddings.embed_query(query)
        return self._rank_documents(query_vec, k)

    async def asimilarity_search(self, query: str, k: int = 4) -> list[Document]:
        if not self._records:
            return []
        if hasattr(self.embeddings, "aembed_query"):
            res = self.embeddings.aembed_query(query)
            query_vec = await res if inspect.isawaitable(res) else res
        else:
            query_vec = self.embeddings.embed_query(query)
        return self._rank_documents(query_vec, k)

    def as_retriever(self, search_kwargs: dict[str, Any] | None = None) -> Any:
        return InMemoryVectorStoreRetriever(
            store=self,
            search_kwargs=dict(search_kwargs or {}),
        )


class InMemoryVectorStoreRetriever(BaseRetriever):
    """LangChain-compatible retriever wrapping InMemoryVectorStoreAdapter."""

    store: VectorStoreAdapter = Field(exclude=True)
    search_kwargs: dict[str, Any] = Field(default_factory=dict)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        k = self.search_kwargs.get("k", 4)
        return self.store.similarity_search(query, k=k)

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        k = self.search_kwargs.get("k", 4)
        return await self.store.asimilarity_search(query, k=k)


class PineconeVectorStoreAdapter:
    """Production vector store adapter connecting to Pinecone Serverless."""

    def __init__(
        self,
        settings: Settings | None = None,
        index_name: str | None = None,
        namespace: str | None = None,
        embeddings: Embeddings | None = None,
        client: Any | None = None,
        dimension: int | None = None,
        index: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.index_name = index_name or getattr(self.settings, "pinecone_index_name", "industry-agentic-rag-kb")
        self.namespace = namespace if namespace is not None else getattr(self.settings, "pinecone_namespace", None)

        # 1. Resolve embeddings
        if embeddings is not None:
            self.embeddings = embeddings
        else:
            api_key = (
                self.settings.embed_api_key.get_secret_value()
                if hasattr(self.settings, "embed_api_key") and self.settings.embed_api_key
                else None
            )
            if not api_key:
                raise ConfigurationError("embed_api_key is required to initialize embeddings for PineconeVectorStoreAdapter")
            self.embeddings = OpenAIEmbeddings(
                model=self.settings.embed_model,
                base_url=self.settings.embed_base_url,
                api_key=self.settings.embed_api_key,
            )

        # 2. Resolve desired vector dimension
        if dimension is not None:
            self.dimension = dimension
        elif hasattr(self.embeddings, "dimension") and isinstance(getattr(self.embeddings, "dimension"), int):
            self.dimension = self.embeddings.dimension
        else:
            self.dimension = get_embedding_dimension(getattr(self.settings, "embed_model", None), settings=self.settings)

        # 3. Resolve Pinecone client
        if client is not None:
            self._pc = client
        else:
            pinecone_key = (
                self.settings.pinecone_api_key.get_secret_value()
                if hasattr(self.settings, "pinecone_api_key") and self.settings.pinecone_api_key
                else ""
            )
            if not pinecone_key.strip():
                raise ConfigurationError("PINECONE_API_KEY is missing or unconfigured.")
            self._pc = Pinecone(api_key=pinecone_key)

        # 4. Non-destructive index validation
        if index is not None:
            self.index = index
        else:
            self.index = self._validate_and_get_index(self._pc, self.index_name, self.dimension)

        # 5. Initialize underlying PineconeVectorStore
        self._store = PineconeVectorStore(
            index=self.index,
            embedding=self.embeddings,
            namespace=self.namespace,
        )

    def _validate_and_get_index(self, pc: Any, index_name: str, desired_dimension: int) -> Any:
        try:
            has_idx = pc.has_index(name=index_name)
        except TypeError:
            has_idx = pc.has_index(index_name)

        if has_idx:
            try:
                index_info = pc.describe_index(name=index_name)
            except TypeError:
                index_info = pc.describe_index(index_name)

            current_dimension = getattr(index_info, "dimension", None)
            if current_dimension is None and isinstance(index_info, dict):
                current_dimension = index_info.get("dimension")

            if current_dimension is not None and current_dimension != desired_dimension:
                raise ConfigurationError(
                    f"Pinecone index '{index_name}' dimension mismatch: "
                    f"existing index has dimension {current_dimension}, "
                    f"but embedding model requires dimension {desired_dimension}. "
                    "Non-destructive validation prevented index deletion."
                )
        else:
            pc.create_index(
                name=index_name,
                dimension=desired_dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )

        try:
            return pc.Index(name=index_name)
        except TypeError:
            return pc.Index(index_name)

    def add_documents(self, documents: list[Document], ids: list[str] | None = None, **kwargs: Any) -> list[str]:
        if not documents:
            return []
        call_kwargs = dict(kwargs)
        if ids is not None:
            call_kwargs["ids"] = ids
        return self._store.add_documents(documents, **call_kwargs)

    async def aadd_documents(self, documents: list[Document], ids: list[str] | None = None, **kwargs: Any) -> list[str]:
        if not documents:
            return []
        call_kwargs = dict(kwargs)
        if ids is not None:
            call_kwargs["ids"] = ids
        return await self._store.aadd_documents(documents, **call_kwargs)

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        return self._store.similarity_search(query, k=k, **kwargs)

    async def asimilarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        return await self._store.asimilarity_search(query, k=k, **kwargs)

    def as_retriever(self, search_kwargs: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        merged = dict(search_kwargs or {})
        merged.update(kwargs)
        return self._store.as_retriever(search_kwargs=merged)


_knowledge_store_lock = threading.RLock()
_knowledge_store_instances: dict[str, VectorStoreAdapter] = {}


def _is_testing_environment() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def reset_knowledge_store() -> None:
    """Reset cached knowledge store singletons (primarily for test isolation)."""
    with _knowledge_store_lock:
        _knowledge_store_instances.clear()


def get_knowledge_store(
    settings: Settings | None = None,
    store_type: str | None = None,
) -> VectorStoreAdapter:
    """Thread-safe factory resolving the configured VectorStoreAdapter singleton."""
    cfg = settings or get_settings()

    resolved_type = store_type
    if resolved_type is None:
        has_pinecone_key = bool(
            hasattr(cfg, "pinecone_api_key")
            and cfg.pinecone_api_key
            and cfg.pinecone_api_key.get_secret_value().strip()
        )
        configured_type = getattr(cfg, "vector_store_type", None)
        if configured_type == "memory":
            resolved_type = "memory"
        elif not has_pinecone_key:
            resolved_type = "memory"
        elif _is_testing_environment() and getattr(cfg, "app_env", "") != "production":
            resolved_type = "memory"
        else:
            resolved_type = configured_type or "pinecone"

    normalized_type = resolved_type.lower().strip()

    with _knowledge_store_lock:
        if settings is None and normalized_type in _knowledge_store_instances:
            return _knowledge_store_instances[normalized_type]

        adapter: VectorStoreAdapter
        if normalized_type == "memory":
            adapter = InMemoryVectorStoreAdapter()
        elif normalized_type == "pinecone":
            adapter = PineconeVectorStoreAdapter(settings=cfg)
        else:
            raise ConfigurationError(f"Unsupported vector store type: '{resolved_type}'")

        if settings is None:
            _knowledge_store_instances[normalized_type] = adapter

        return adapter


