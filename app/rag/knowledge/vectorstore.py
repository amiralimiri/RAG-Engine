"""In-memory vector store adapter and offline embedding doubles."""

import math
import re
import hashlib
import inspect
import uuid
from typing import Any
from pydantic import Field
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.callbacks import CallbackManagerForRetrieverRun, AsyncCallbackManagerForRetrieverRun
from langchain_core.retrievers import BaseRetriever

from app.rag.knowledge.base import VectorStoreAdapter


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

