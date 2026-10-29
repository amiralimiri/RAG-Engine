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

    output = tool.invoke({"query": "fastapi"})
    assert "Search answer: FastAPI is a modern web framework." in output
    assert "Title: FastAPI Tutorial" in output
    assert "URL: https://fastapi.tiangolo.com" in output
    assert "Content: Learn how to build high performance APIs." in output
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
    output = await tool.ainvoke({"query": "python"})
    assert "Title: Python 3.14 Docs" in output
    assert "URL: https://docs.python.org" in output
    mock_search.ainvoke.assert_called_once()


def test_create_web_search_tool_string_output():
    mock_search = MagicMock()
    mock_search.invoke.return_value = "Direct raw string search result."

    tool = create_web_search_tool(search_client=mock_search)
    output = tool.invoke({"query": "raw"})
    assert output == "Direct raw string search result."
