"""Unit tests for the modular Tool Abstraction Layer."""

import asyncio
from unittest.mock import MagicMock, AsyncMock
import pytest
from langchain_core.documents import Document
from langchain_core.tools import BaseTool

from app.rag.tools import (
    KnowledgeSearchInput,
    WebSearchInput,
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
    mock_search.ainvoke = AsyncMock(return_value={
        "results": [
            {
                "title": "Python 3.14 Docs",
                "url": "https://docs.python.org",
                "content": "Official Python documentation.",
            }
        ]
    })

    tool = create_web_search_tool(search_client=mock_search)
    result = await tool.ainvoke({"query": "python"})
    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result

    assert "Title: Python 3.14 Docs" in content
    assert "URL: https://docs.python.org" in content
    assert "Content: Official Python documentation." in content
    assert artifact["citations"] == [
        {"title": "Python 3.14 Docs", "url": "https://docs.python.org", "type": "web"}
    ]
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

        monkeypatch.setattr(engine_mod, "get_retriever", bogus_get)

    tool = create_retriever_tool()
    content, artifact = tool.invoke({"query": "policy"})
    assert "Zero reflection chunk." in content


