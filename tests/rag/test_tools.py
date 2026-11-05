"""Unit tests for the modular Tool Abstraction Layer."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.rag.tools import (
    AggregatedEvidence,
    Citation,
    KnowledgeSearchInput,
    ToolEvidenceArtifact,
    ToolResult,
    WebSearchInput,
    aggregate_tool_evidence,
    create_retriever_tool,
    create_web_search_tool,
)


def test_input_schemas():
    kb_input = KnowledgeSearchInput(query="how to connect to vpn")
    assert kb_input.query == "how to connect to vpn"

    web_input = WebSearchInput(query="docker desktop download")
    assert web_input.query == "docker desktop download"


def test_create_retriever_tool_sync():
    docs = [
        Document(page_content="Use OpenVPN config file.", metadata={"source": "docs/vpn.md"}),
        Document(page_content="Contact IT for credentials.", metadata={"source": "docs/help.md"}),
    ]
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = docs

    tool = create_retriever_tool(retriever=mock_retriever)
    assert isinstance(tool, BaseTool)
    assert tool.name == "knowledge_base_search"
    assert "query" in tool.args

    result = tool.invoke({"query": "vpn"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert "[Source: docs/vpn.md]" in content
    assert "Use OpenVPN config file." in content
    assert "[Source: docs/help.md]" in content
    assert artifact["documents"] == docs
    assert artifact["citations"] == [
        {"title": "vpn.md", "url": "", "type": "private_kb"},
        {"title": "help.md", "url": "", "type": "private_kb"},
    ]
    mock_retriever.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_create_retriever_tool_async():
    docs = [
        Document(page_content="SSO login with Okta.", metadata={"source": "sso.pdf"}),
    ]
    mock_retriever = MagicMock()
    mock_retriever.ainvoke = AsyncMock(return_value=docs)

    tool = create_retriever_tool(retriever=mock_retriever)
    result = await tool.ainvoke({"query": "sso"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert "[Source: sso.pdf]" in content
    assert "SSO login with Okta." in content
    assert artifact["documents"] == docs
    assert artifact["citations"] == [
        {"title": "sso.pdf", "url": "", "type": "private_kb"},
    ]
    mock_retriever.ainvoke.assert_called_once()


def test_create_retriever_tool_empty():
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = []

    tool = create_retriever_tool(retriever=mock_retriever)
    result = tool.invoke({"query": "nonexistent"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert "No relevant internal documentation found." in content
    assert artifact["documents"] == []
    assert artifact["citations"] == []


def test_create_retriever_tool_sync_exception_propagation():
    mock_retriever = MagicMock()
    mock_retriever.invoke.side_effect = ConnectionError("Pinecone connection timed out")

    tool = create_retriever_tool(retriever=mock_retriever)
    with pytest.raises(ConnectionError, match="Pinecone connection timed out"):
        tool.invoke({"query": "vpn"})


@pytest.mark.asyncio
async def test_create_retriever_tool_async_exception_propagation():
    mock_retriever = MagicMock()
    mock_retriever.ainvoke = AsyncMock(side_effect=ConnectionError("Pinecone connection timed out"))

    tool = create_retriever_tool(retriever=mock_retriever)
    with pytest.raises(ConnectionError, match="Pinecone connection timed out"):
        await tool.ainvoke({"query": "vpn"})


def test_create_web_search_tool_sync():
    mock_search = MagicMock()
    mock_search.invoke.return_value = {
        "answer": "FastAPI is a modern web framework.",
        "results": [
            {
                "title": "FastAPI Tutorial",
                "url": "https://fastapi.tiangolo.com",
                "content": "Learn how to build high performance APIs.",
            }
        ],
    }

    tool = create_web_search_tool(search_client=mock_search)
    assert isinstance(tool, BaseTool)
    assert tool.name == "web_search"
    assert "query" in tool.args

    result = tool.invoke({"query": "fastapi"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert "Search answer: FastAPI is a modern web framework." in content
    assert "Title: FastAPI Tutorial" in content
    assert "URL: https://fastapi.tiangolo.com" in content
    assert "Content: Learn how to build high performance APIs." in content
    assert artifact["citations"] == [
        {"title": "FastAPI Tutorial", "url": "https://fastapi.tiangolo.com", "type": "web"}
    ]
    assert artifact["retrieved_contexts"] == [
        "Title: FastAPI Tutorial\nURL: https://fastapi.tiangolo.com\nContent: Learn how to build high performance APIs."
    ]
    mock_search.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_create_web_search_tool_async():
    mock_search = MagicMock()
    mock_search.ainvoke = AsyncMock(
        return_value={
            "results": [
                {
                    "title": "Python 3.14 Docs",
                    "url": "https://docs.python.org",
                    "content": "Official Python documentation.",
                }
            ]
        }
    )

    tool = create_web_search_tool(search_client=mock_search)
    result = await tool.ainvoke({"query": "python"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert "Title: Python 3.14 Docs" in content
    assert "URL: https://docs.python.org" in content
    assert "Content: Official Python documentation." in content
    assert artifact["citations"] == [{"title": "Python 3.14 Docs", "url": "https://docs.python.org", "type": "web"}]
    assert artifact["retrieved_contexts"] == [
        "Title: Python 3.14 Docs\nURL: https://docs.python.org\nContent: Official Python documentation."
    ]
    mock_search.ainvoke.assert_called_once()


def test_create_web_search_tool_empty():
    mock_search = MagicMock()
    mock_search.invoke.return_value = {"results": []}

    tool = create_web_search_tool(search_client=mock_search)
    result = tool.invoke({"query": "nonexistent"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert content == "No relevant web search results found."
    assert artifact["citations"] == []
    assert artifact["retrieved_contexts"] == []


def test_create_web_search_tool_string_output():
    mock_search = MagicMock()
    mock_search.invoke.return_value = "Direct raw string search result."

    tool = create_web_search_tool(search_client=mock_search)
    result = tool.invoke({"query": "raw"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert content == "Direct raw string search result."
    assert artifact["citations"] == []
    assert artifact["retrieved_contexts"] == ["Direct raw string search result."]


def test_create_web_search_tool_sync_exception_propagation():
    mock_search = MagicMock()
    mock_search.invoke.side_effect = RuntimeError("Tavily API rate limit exceeded")

    tool = create_web_search_tool(search_client=mock_search)
    with pytest.raises(RuntimeError, match="Tavily API rate limit exceeded"):
        tool.invoke({"query": "rate_limited"})


@pytest.mark.asyncio
async def test_create_web_search_tool_async_exception_propagation():
    mock_search = MagicMock()
    mock_search.ainvoke = AsyncMock(side_effect=RuntimeError("Tavily API rate limit exceeded"))

    tool = create_web_search_tool(search_client=mock_search)
    with pytest.raises(RuntimeError, match="Tavily API rate limit exceeded"):
        await tool.ainvoke({"query": "rate_limited"})


def test_create_retriever_tool_default_retriever_uses_knowledge_store(monkeypatch: pytest.MonkeyPatch):
    """create_retriever_tool() without arguments obtains retriever from get_knowledge_store().as_retriever()."""
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = [
        Document(page_content="Hardware policy chunk.", metadata={"source": "hardware.md"})
    ]

    mock_store = MagicMock()
    mock_store.as_retriever.return_value = mock_retriever

    monkeypatch.setattr("app.rag.knowledge.get_knowledge_store", lambda **kw: mock_store)

    tool = create_retriever_tool()
    content, artifact = tool.invoke({"query": "laptop replacement"})

    assert "Hardware policy chunk." in content
    assert artifact["citations"] == [{"title": "hardware.md", "url": "", "type": "private_kb"}]
    mock_store.as_retriever.assert_called_once()
    mock_retriever.invoke.assert_called_once()


def test_create_retriever_tool_without_engine_module_reflection(monkeypatch: pytest.MonkeyPatch):
    """create_retriever_tool does not depend on sys.modules['app.rag.engine']."""
    import sys

    # Even if app.rag.engine has a dummy retriever or is missing, get_knowledge_store() is used directly
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = [
        Document(page_content="Zero reflection chunk.", metadata={"source": "policy.md"})
    ]

    mock_store = MagicMock()
    mock_store.as_retriever.return_value = mock_retriever

    monkeypatch.setattr("app.rag.knowledge.get_knowledge_store", lambda **kw: mock_store)

    # Inject a bogus get_retriever on app.rag.engine if present
    engine_mod = sys.modules.get("app.rag.engine")
    if engine_mod is not None:
        bogus_called = False

        def bogus_get():
            nonlocal bogus_called
            bogus_called = True
            raise AssertionError("Reflection hack was called on app.rag.engine!")

        monkeypatch.setattr(engine_mod, "get_retriever", bogus_get, raising=False)

    tool = create_retriever_tool()
    content, artifact = tool.invoke({"query": "policy"})
    assert "Zero reflection chunk." in content


def test_create_web_search_tool_without_engine_module_reflection(monkeypatch: pytest.MonkeyPatch):
    """create_web_search_tool does not depend on sys.modules['app.rag.engine']."""
    import sys

    # Inject a bogus web_search_tool on app.rag.engine if present
    engine_mod = sys.modules.get("app.rag.engine")
    if engine_mod is not None:

        def bogus_tool():
            raise AssertionError("Reflection hack was called on app.rag.engine!")

        monkeypatch.setattr(engine_mod, "web_search_tool", bogus_tool, raising=False)

    mock_client = MagicMock()
    mock_client.invoke.return_value = {
        "answer": "Clean search answer",
        "results": [{"title": "Web Title", "url": "https://example.com", "content": "Clean snippet"}],
    }

    tool = create_web_search_tool(search_client=mock_client)
    content, artifact = tool.invoke({"query": "test query"})
    assert "Clean search answer" in content
    assert artifact["citations"] == [{"title": "Web Title", "url": "https://example.com", "type": "web"}]


# ============================================================================
# Ticket 01: Canonical Domain Models & Polymorphic Evidence Aggregator Tests
# ============================================================================


def test_citation_domain_model():
    # Default fields
    c1 = Citation(title="Guide.md")
    assert c1.title == "Guide.md"
    assert c1.url == ""
    assert c1.type == "general"

    # Explicit fields
    c2 = Citation(title="API Docs", url="https://docs.example.com", type="web")
    assert c2.title == "API Docs"
    assert c2.url == "https://docs.example.com"
    assert c2.type == "web"

    # Domain validation failure when required field 'title' is missing
    with pytest.raises(ValidationError):
        Citation.model_validate({})

    # Frozen/immutable domain model
    with pytest.raises(ValidationError):
        c2.title = "Mutated"


def test_tool_evidence_artifact_and_subscripting_fallback():
    # Defaults
    art_default = ToolEvidenceArtifact()
    assert art_default.citations == []
    assert art_default.retrieved_contexts == []
    assert art_default.source_used == "direct"
    assert art_default.raw_payload is None

    # Subscripting fallback and get()
    citation = Citation(title="vpn.md", url="", type="private_kb")
    art = ToolEvidenceArtifact(
        citations=[citation],
        retrieved_contexts=["VPN connection steps"],
        source_used="vectorstore",
        raw_payload={"custom": "doc_payload"},
    )

    assert art["citations"] == [citation]
    assert art["retrieved_contexts"] == ["VPN connection steps"]
    assert art["source_used"] == "vectorstore"
    assert art["raw_payload"] == {"custom": "doc_payload"}
    assert art.get("source_used") == "vectorstore"
    assert art.get("nonexistent", "default_val") == "default_val"

    with pytest.raises(KeyError):
        _ = art["unknown_field"]

    # Field-only subscripting: internal Pydantic attributes are not exposed
    with pytest.raises(KeyError):
        _ = art["model_fields"]
    assert art.get("model_fields", "default_val") == "default_val"


def test_tool_result_container_and_tuple_unpacking():
    artifact = ToolEvidenceArtifact(
        retrieved_contexts=["Some context"],
        source_used="vectorstore",
    )
    result = ToolResult(content="Retrieved content text", artifact=artifact)

    # Direct attributes
    assert result.content == "Retrieved content text"
    assert result.artifact is artifact

    # 2-tuple unpacking
    content, art = result
    assert content == "Retrieved content text"
    assert art is artifact

    # Validation failure when required fields are missing
    with pytest.raises(ValidationError):
        ToolResult.model_validate({"content": "only content"})


def test_aggregated_evidence_domain_model():
    evidence = AggregatedEvidence(
        citations=[Citation(title="Doc A")],
        retrieved_contexts=["Context 1"],
        source_used="vectorstore",
    )
    assert len(evidence.citations) == 1
    assert evidence.citations[0].title == "Doc A"
    assert evidence.retrieved_contexts == ["Context 1"]
    assert evidence.source_used == "vectorstore"

    # Default values
    empty_evidence = AggregatedEvidence()
    assert empty_evidence.citations == []
    assert empty_evidence.retrieved_contexts == []
    assert empty_evidence.source_used == "direct"

    # Frozen/immutable domain model
    with pytest.raises(ValidationError):
        evidence.source_used = "other"


def test_aggregate_tool_evidence_empty_and_non_tool_messages():
    # Empty sequence
    ev1 = aggregate_tool_evidence([])
    assert ev1.citations == []
    assert ev1.retrieved_contexts == []
    assert ev1.source_used == "direct"

    # Sequence with no tool messages or artifacts
    ev2 = aggregate_tool_evidence([HumanMessage(content="hello"), AIMessage(content="hi")])
    assert ev2.citations == []
    assert ev2.retrieved_contexts == []
    assert ev2.source_used == "direct"


def test_aggregate_tool_evidence_single_source_uniform():
    art1 = ToolEvidenceArtifact(
        citations=[Citation(title="kb1.md", type="private_kb")],
        retrieved_contexts=["Context KB 1"],
        source_used="vectorstore",
    )
    art2 = ToolEvidenceArtifact(
        citations=[Citation(title="kb2.md", type="private_kb")],
        retrieved_contexts=["Context KB 2"],
        source_used="vectorstore",
    )

    ev = aggregate_tool_evidence([art1, art2])
    assert len(ev.citations) == 2
    assert ev.citations[0].title == "kb1.md"
    assert ev.citations[1].title == "kb2.md"
    assert ev.retrieved_contexts == ["Context KB 1", "Context KB 2"]
    assert ev.source_used == "vectorstore"


def test_aggregate_tool_evidence_extracts_from_tool_messages():
    art = ToolEvidenceArtifact(
        citations=[Citation(title="Search Result", url="https://example.com", type="web")],
        retrieved_contexts=["Web context snippet"],
        source_used="web_search",
    )
    msg = ToolMessage(content="Display string", artifact=art, tool_call_id="call_web_1")

    ev = aggregate_tool_evidence([msg])
    assert len(ev.citations) == 1
    assert ev.citations[0].title == "Search Result"
    assert ev.citations[0].url == "https://example.com"
    assert ev.retrieved_contexts == ["Web context snippet"]
    assert ev.source_used == "web_search"


def test_aggregate_tool_evidence_citation_deduplication():
    art1 = ToolEvidenceArtifact(
        citations=[
            Citation(title="Duplicate Doc", url="https://dup.com", type="web"),
            Citation(title="Unique Doc 1", url="https://u1.com", type="web"),
        ],
        retrieved_contexts=["Snippet 1", ""],
        source_used="web_search",
    )
    art2 = ToolEvidenceArtifact(
        citations=[
            Citation(title="Duplicate Doc", url="https://dup.com", type="web"),
            Citation(title="Unique Doc 2", url="https://u2.com", type="web"),
        ],
        retrieved_contexts=["Snippet 2"],
        source_used="web_search",
    )

    ev = aggregate_tool_evidence([art1, art2])
    assert len(ev.citations) == 3
    assert [c.title for c in ev.citations] == ["Duplicate Doc", "Unique Doc 1", "Unique Doc 2"]
    assert ev.retrieved_contexts == ["Snippet 1", "", "Snippet 2"]


def test_aggregate_tool_evidence_multiple_distinct_sources_resolves_react_agent():
    art_kb = ToolEvidenceArtifact(
        citations=[Citation(title="kb.md", type="private_kb")],
        retrieved_contexts=["KB context snippet"],
        source_used="vectorstore",
    )
    art_web = ToolEvidenceArtifact(
        citations=[Citation(title="Web Result", url="https://web.com", type="web")],
        retrieved_contexts=["Web context snippet"],
        source_used="web_search",
    )

    ev = aggregate_tool_evidence([art_kb, art_web])
    assert len(ev.citations) == 2
    assert ev.retrieved_contexts == ["KB context snippet", "Web context snippet"]
    assert ev.source_used == "react_agent"
