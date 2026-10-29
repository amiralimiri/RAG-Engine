"""Tests for the knowledge store vector adapter seam and in-memory double."""

import pytest
from langchain_core.documents import Document

from app.rag.knowledge.base import IndexingResult, VectorStoreAdapter
from app.rag.knowledge.vectorstore import InMemoryVectorStoreAdapter, OfflineDeterministicEmbeddings


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






