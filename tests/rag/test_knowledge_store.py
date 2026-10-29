"""Tests for the knowledge store vector adapter seam and in-memory double."""

import pytest
from langchain_core.documents import Document

from app.rag.knowledge.base import ConfigurationError, IndexingResult, VectorStoreAdapter
from app.rag.knowledge.vectorstore import (
    InMemoryVectorStoreAdapter,
    OfflineDeterministicEmbeddings,
    PineconeVectorStoreAdapter,
    get_knowledge_store,
    reset_knowledge_store,
)


def test_in_memory_adapter_satisfies_vector_store_adapter_protocol() -> None:
    """Verify InMemoryVectorStoreAdapter implements the runtime checkable VectorStoreAdapter protocol."""
    adapter = InMemoryVectorStoreAdapter()
    assert isinstance(adapter, VectorStoreAdapter)


def test_indexing_result_model_defaults() -> None:
    """Verify IndexingResult schema defaults and attributes."""
    res = IndexingResult(source="test.pdf")
    assert res.source == "test.pdf"
    assert res.document_count == 1
    assert res.chunk_count == 0
    assert res.ids_created == []
    assert res.success is True
    assert res.error is None


def test_in_memory_adapter_sync_add_and_similarity_search() -> None:
    """Verify synchronous add_documents and exact cosine similarity ranking with top-k filtering."""
    adapter = InMemoryVectorStoreAdapter()
    docs = [
        Document(
            page_content="To reset your corporate VPN password, open the IT self-service portal.",
            metadata={"source": "vpn_reset.md"},
        ),
        Document(
            page_content="The corporate cafeteria opens from 11:30 AM to 2:00 PM for lunch.",
            metadata={"source": "cafeteria.md"},
        ),
        Document(
            page_content="VPN configuration profiles can be downloaded from the IT software center.",
            metadata={"source": "vpn_config.md"},
        ),
    ]

    ids = adapter.add_documents(docs)
    assert len(ids) == 3
    assert len(set(ids)) == 3

    # Similarity search top-k=2
    results = adapter.similarity_search("reset corporate VPN password", k=2)
    assert len(results) == 2
    assert results[0].metadata["source"] == "vpn_reset.md"
    assert results[1].metadata["source"] == "vpn_config.md"

    # Verify deterministic custom IDs and idempotent updates
    custom_doc = Document(page_content="New VPN policy content", metadata={"source": "policy.md"})
    custom_ids = adapter.add_documents([custom_doc], ids=["policy_chunk_1"])
    assert custom_ids == ["policy_chunk_1"]

    updated_doc = Document(page_content="Updated VPN policy content strictly overriding previous", metadata={"source": "policy.md"})
    adapter.add_documents([updated_doc], ids=["policy_chunk_1"])
    policy_results = adapter.similarity_search("strictly overriding previous", k=1)
    assert len(policy_results) == 1
    assert policy_results[0].page_content == "Updated VPN policy content strictly overriding previous"


@pytest.mark.asyncio
async def test_in_memory_adapter_async_parity() -> None:
    """Verify asynchronous aadd_documents and asimilarity_search produce identical results to sync."""
    sync_adapter = InMemoryVectorStoreAdapter()
    async_adapter = InMemoryVectorStoreAdapter()

    docs = [
        Document(
            page_content="Submit an IT service request for any hardware replacement.",
            metadata={"source": "hardware.md"},
        ),
        Document(
            page_content="Use SSH key authentication to log into production servers.",
            metadata={"source": "ssh.md"},
        ),
        Document(
            page_content="Hardware inventory audits are conducted every quarter.",
            metadata={"source": "audit.md"},
        ),
    ]

    sync_ids = sync_adapter.add_documents(docs, ids=["hw_1", "ssh_1", "audit_1"])
    async_ids = await async_adapter.aadd_documents(docs, ids=["hw_1", "ssh_1", "audit_1"])

    assert async_ids == sync_ids

    query = "hardware request replacement"
    sync_results = sync_adapter.similarity_search(query, k=2)
    async_results = await async_adapter.asimilarity_search(query, k=2)

    assert len(async_results) == len(sync_results) == 2
    assert [d.page_content for d in async_results] == [d.page_content for d in sync_results]
    assert [d.metadata for d in async_results] == [d.metadata for d in sync_results]


@pytest.mark.asyncio
async def test_in_memory_adapter_as_retriever() -> None:
    """Verify as_retriever returns an object supporting both invoke and ainvoke."""
    adapter = InMemoryVectorStoreAdapter()
    docs = [
        Document(
            page_content="To reset your corporate VPN password, open the IT self-service portal.",
            metadata={"source": "vpn_reset.md"},
        ),
        Document(
            page_content="The corporate cafeteria opens from 11:30 AM to 2:00 PM for lunch.",
            metadata={"source": "cafeteria.md"},
        ),
        Document(
            page_content="VPN configuration profiles can be downloaded from the IT software center.",
            metadata={"source": "vpn_config.md"},
        ),
    ]
    adapter.add_documents(docs)

    retriever = adapter.as_retriever(search_kwargs={"k": 2})
    assert retriever is not None
    assert hasattr(retriever, "invoke")
    assert hasattr(retriever, "ainvoke")

    # Synchronous retrieval via invoke
    sync_results = retriever.invoke("corporate VPN password reset")
    assert len(sync_results) == 2
    assert sync_results[0].metadata["source"] == "vpn_reset.md"

    # Asynchronous retrieval via ainvoke
    async_results = await retriever.ainvoke("corporate VPN password reset")
    assert len(async_results) == 2
    assert async_results[0].metadata["source"] == "vpn_reset.md"
    assert [d.page_content for d in async_results] == [d.page_content for d in sync_results]


@pytest.mark.asyncio
async def test_in_memory_retriever_tool_integration() -> None:
    """Verify InMemoryVectorStoreAdapter retriever integrates seamlessly with create_retriever_tool."""
    from app.rag.tools.retriever import create_retriever_tool

    adapter = InMemoryVectorStoreAdapter()
    adapter.add_documents([
        Document(page_content="Global VPN instructions and access procedures", metadata={"source": "vpn.md"}),
    ])
    tool = create_retriever_tool(retriever=adapter.as_retriever(search_kwargs={"k": 1}))

    # Sync invocation
    content, artifact = tool.invoke({"query": "VPN access instructions"})
    assert "Global VPN instructions" in content
    assert len(artifact["documents"]) == 1

    # Async invocation
    content_async, artifact_async = await tool.ainvoke({"query": "VPN access instructions"})
    assert "Global VPN instructions" in content_async
    assert len(artifact_async["documents"]) == 1


def test_in_memory_adapter_edge_cases() -> None:
    """Verify empty inputs, mismatched id lengths, and custom embedding injection."""
    adapter = InMemoryVectorStoreAdapter()

    # Empty document list
    assert adapter.add_documents([]) == []
    assert adapter.similarity_search("anything") == []

    # Mismatched id length
    with pytest.raises(ValueError, match="does not match number of documents"):
        adapter.add_documents([Document(page_content="test")], ids=["id1", "id2"])

    # Custom mock embeddings injection
    class CustomEmbeddings(OfflineDeterministicEmbeddings):
        pass

    custom_emb = CustomEmbeddings(dimension=64)
    custom_adapter = InMemoryVectorStoreAdapter(embeddings=custom_emb)
    assert custom_adapter.embeddings is custom_emb


@pytest.mark.asyncio
async def test_in_memory_adapter_async_edge_cases() -> None:
    """Verify async empty inputs and mismatched id lengths."""
    adapter = InMemoryVectorStoreAdapter()

    assert await adapter.aadd_documents([]) == []
    assert await adapter.asimilarity_search("anything") == []

    with pytest.raises(ValueError, match="does not match number of documents"):
        await adapter.aadd_documents([Document(page_content="test")], ids=["id1", "id2"])


def test_pinecone_adapter_satisfies_vector_store_adapter_protocol() -> None:
    """Verify PineconeVectorStoreAdapter implements runtime checkable VectorStoreAdapter protocol."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.has_index.return_value = True
    mock_client.describe_index.return_value.dimension = 128

    adapter = PineconeVectorStoreAdapter(
        client=mock_client,
        dimension=128,
        embeddings=OfflineDeterministicEmbeddings(dimension=128),
        index_name="test-index",
    )
    assert isinstance(adapter, VectorStoreAdapter)


def test_pinecone_adapter_dimension_mismatch_raises_configuration_error_without_deleting() -> None:
    """Verify non-destructive index validation raises ConfigurationError without deleting the index."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.has_index.return_value = True
    mock_client.describe_index.return_value.dimension = 1536

    # Model requires dimension 3072, but existing cloud index is 1536
    with pytest.raises(ConfigurationError, match="dimension mismatch"):
        PineconeVectorStoreAdapter(
            client=mock_client,
            dimension=3072,
            embeddings=OfflineDeterministicEmbeddings(dimension=3072),
            index_name="test-mismatch-index",
        )

    # Assert non-destructive invariant: delete_index must NOT be called
    mock_client.delete_index.assert_not_called()


def test_pinecone_adapter_creates_index_if_missing() -> None:
    """Verify missing index triggers create_index with expected dimension without polling loops."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.has_index.return_value = False

    adapter = PineconeVectorStoreAdapter(
        client=mock_client,
        dimension=128,
        embeddings=OfflineDeterministicEmbeddings(dimension=128),
        index_name="new-index",
    )
    mock_client.create_index.assert_called_once()
    assert mock_client.create_index.call_args.kwargs.get("dimension") == 128
    assert mock_client.create_index.call_args.kwargs.get("name") == "new-index"
    mock_client.delete_index.assert_not_called()
    assert isinstance(adapter, VectorStoreAdapter)


@pytest.mark.asyncio
async def test_pinecone_adapter_delegation_methods() -> None:
    """Verify sync and async CRUD methods delegate cleanly to underlying vector store."""
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.has_index.return_value = True
    mock_client.describe_index.return_value.dimension = 128

    adapter = PineconeVectorStoreAdapter(
        client=mock_client,
        dimension=128,
        embeddings=OfflineDeterministicEmbeddings(dimension=128),
        index_name="test-index",
    )

    # Mock the internal _store to verify delegation
    mock_store = MagicMock()
    mock_store.add_documents.return_value = ["id1", "id2"]
    mock_store.aadd_documents = AsyncMock(return_value=["id1", "id2"])
    mock_store.similarity_search.return_value = [Document(page_content="result")]
    mock_store.asimilarity_search = AsyncMock(return_value=[Document(page_content="async result")])
    mock_retriever = MagicMock()
    mock_store.as_retriever.return_value = mock_retriever

    adapter._store = mock_store

    docs = [Document(page_content="doc1"), Document(page_content="doc2")]

    # add_documents
    ids = adapter.add_documents(docs, ids=["id1", "id2"])
    assert ids == ["id1", "id2"]
    mock_store.add_documents.assert_called_once_with(docs, ids=["id1", "id2"])

    # aadd_documents
    aids = await adapter.aadd_documents(docs, ids=["id1", "id2"])
    assert aids == ["id1", "id2"]
    mock_store.aadd_documents.assert_called_once_with(docs, ids=["id1", "id2"])

    # similarity_search
    results = adapter.similarity_search("query text", k=3)
    assert len(results) == 1
    mock_store.similarity_search.assert_called_once_with("query text", k=3)

    # asimilarity_search
    aresults = await adapter.asimilarity_search("async query text", k=5)
    assert len(aresults) == 1
    mock_store.asimilarity_search.assert_called_once_with("async query text", k=5)

    # as_retriever
    retriever = adapter.as_retriever(search_kwargs={"k": 2})
    assert retriever is mock_retriever
    mock_store.as_retriever.assert_called_once_with(search_kwargs={"k": 2})


def test_get_knowledge_store_factory() -> None:
    """Verify get_knowledge_store resolves memory and pinecone adapters and provides singleton caching."""
    from unittest.mock import patch, MagicMock

    reset_knowledge_store()

    # Memory store resolution
    mem_store = get_knowledge_store(store_type="memory")
    assert isinstance(mem_store, InMemoryVectorStoreAdapter)

    # Thread-safe singleton returns identical instance
    mem_store_again = get_knowledge_store(store_type="memory")
    assert mem_store is mem_store_again

    # Pinecone store resolution with mocked Pinecone client
    with patch("app.rag.knowledge.vectorstore.Pinecone") as mock_pc_cls, \
         patch("app.rag.knowledge.vectorstore.PineconeVectorStore") as mock_pvs_cls:
        mock_pc = mock_pc_cls.return_value
        mock_pc.has_index.return_value = True
        mock_pc.describe_index.return_value.dimension = 3072

        pinecone_store = get_knowledge_store(store_type="pinecone")
        assert isinstance(pinecone_store, PineconeVectorStoreAdapter)

        pinecone_store_again = get_knowledge_store(store_type="pinecone")
        assert pinecone_store is pinecone_store_again


def test_get_knowledge_store_default_fallback_without_keys() -> None:
    """Verify get_knowledge_store defaults to memory store when pinecone is unconfigured or in tests."""
    from app.core.config import Settings
    from pydantic import SecretStr

    reset_knowledge_store()

    # When pinecone_api_key is empty/missing
    empty_settings = Settings(
        pinecone_api_key=SecretStr(""),
        vector_store_type="pinecone",
    )
    store = get_knowledge_store(settings=empty_settings)
    assert isinstance(store, InMemoryVectorStoreAdapter)


def test_knowledge_store_module_exports() -> None:
    """Verify app.rag.knowledge exposes PineconeVectorStoreAdapter and get_knowledge_store."""
    import app.rag.knowledge as pkg

    assert hasattr(pkg, "PineconeVectorStoreAdapter")
    assert hasattr(pkg, "get_knowledge_store")
    assert hasattr(pkg, "reset_knowledge_store")
    assert hasattr(pkg, "ConfigurationError")
    assert hasattr(pkg, "get_embedding_dimension")
    assert hasattr(pkg, "EMBEDDING_DIMENSIONS")


def test_pinecone_adapter_missing_api_keys_raise_configuration_error() -> None:
    """Verify PineconeVectorStoreAdapter validates required API keys and raises ConfigurationError."""
    from app.core.config import Settings
    from pydantic import SecretStr

    # Missing Pinecone API key
    no_pinecone_settings = Settings(
        pinecone_api_key=SecretStr(""),
    )
    with pytest.raises(ConfigurationError, match="PINECONE_API_KEY is missing or unconfigured"):
        PineconeVectorStoreAdapter(
            settings=no_pinecone_settings,
            embeddings=OfflineDeterministicEmbeddings(dimension=128),
        )

    # Missing Embedding API key when no explicit embeddings passed
    no_embed_settings = Settings(
        embed_api_key=SecretStr(""),
        pinecone_api_key=SecretStr("mock_pinecone_key"),
    )
    with pytest.raises(ConfigurationError, match="embed_api_key is required"):
        PineconeVectorStoreAdapter(
            settings=no_embed_settings,
        )


def test_get_embedding_dimension_behavior() -> None:
    """Verify get_embedding_dimension resolves known models and raises ConfigurationError for invalid ones."""
    from app.rag.knowledge.vectorstore import get_embedding_dimension

    assert get_embedding_dimension("text-embedding-3-small") == 1536
    assert get_embedding_dimension("text-embedding-3-large") == 3072
    assert get_embedding_dimension("text-embedding-ada-002") == 1536
    assert get_embedding_dimension("all-minilm-l6-v2") == 384
    assert get_embedding_dimension("prefix/text-embedding-3-small-custom") == 1536

    with pytest.raises(ConfigurationError, match="Embedding model is not configured"):
        get_embedding_dimension("")

    with pytest.raises(ConfigurationError, match="Unsupported embedding model"):
        get_embedding_dimension("unknown-model-xyz")


def test_get_knowledge_store_unsupported_type() -> None:
    """Verify get_knowledge_store raises ConfigurationError for unsupported store types."""
    with pytest.raises(ConfigurationError, match="Unsupported vector store type"):
        get_knowledge_store(store_type="unsupported_store")


def test_get_knowledge_store_concurrent_singleton_access() -> None:
    """Verify get_knowledge_store safely returns the identical singleton across concurrent threads."""
    import concurrent.futures

    reset_knowledge_store()

    def worker():
        return get_knowledge_store(store_type="memory")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker) for _ in range(30)]
        results = [f.result() for f in futures]

    first = results[0]
    for r in results[1:]:
        assert r is first








