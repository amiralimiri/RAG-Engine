"""Unit tests for the modular Tool Abstraction Layer."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.rag.tools import (
    AggregatedEvidence,
    Citation,
    FakeRAGTool,
    KnowledgeSearchInput,
    RAGTool,
    ToolEvidenceArtifact,
    ToolResult,
    WebSearchInput,
    aggregate_tool_evidence,
    create_retriever_tool,
    create_web_search_tool,
    rag_tool,
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

    result = tool.execute({"query": "vpn"})
    assert isinstance(result, ToolResult)
    content, artifact = result

    assert "[Source: docs/vpn.md]" in content
    assert "Use OpenVPN config file." in content
    assert "[Source: docs/help.md]" in content
    assert artifact.raw_payload == docs
    assert artifact.source_used == "vectorstore"
    assert artifact.citations == [
        Citation(title="vpn.md", url="", type="private_kb"),
        Citation(title="help.md", url="", type="private_kb"),
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
    result = await tool.aexecute({"query": "sso"})
    assert isinstance(result, ToolResult)
    content, artifact = result

    assert "[Source: sso.pdf]" in content
    assert "SSO login with Okta." in content
    assert artifact.raw_payload == docs
    assert artifact.source_used == "vectorstore"
    assert artifact.citations == [
        Citation(title="sso.pdf", url="", type="private_kb"),
    ]
    mock_retriever.ainvoke.assert_called_once()


def test_create_retriever_tool_empty():
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = []

    tool = create_retriever_tool(retriever=mock_retriever)
    result = tool.execute({"query": "nonexistent"})
    assert isinstance(result, ToolResult)
    content, artifact = result

    assert "No relevant internal documentation found." in content
    assert artifact.raw_payload == []
    assert artifact.citations == []
    assert artifact.source_used == "vectorstore"


def test_create_retriever_tool_sync_exception_propagation():
    mock_retriever = MagicMock()
    mock_retriever.invoke.side_effect = ConnectionError("Pinecone connection timed out")

    tool = create_retriever_tool(retriever=mock_retriever)
    with pytest.raises(ConnectionError, match="Pinecone connection timed out"):
        tool.execute({"query": "vpn"})


@pytest.mark.asyncio
async def test_create_retriever_tool_async_exception_propagation():
    mock_retriever = MagicMock()
    mock_retriever.ainvoke = AsyncMock(side_effect=ConnectionError("Pinecone connection timed out"))

    tool = create_retriever_tool(retriever=mock_retriever)
    with pytest.raises(ConnectionError, match="Pinecone connection timed out"):
        await tool.aexecute({"query": "vpn"})


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

    result = tool.execute({"query": "fastapi"})
    assert isinstance(result, ToolResult)
    content, artifact = result

    assert "Search answer: FastAPI is a modern web framework." in content
    assert "Title: FastAPI Tutorial" in content
    assert "URL: https://fastapi.tiangolo.com" in content
    assert "Content: Learn how to build high performance APIs." in content
    assert artifact.source_used == "web_search"
    assert artifact.citations == [Citation(title="FastAPI Tutorial", url="https://fastapi.tiangolo.com", type="web")]
    assert artifact.retrieved_contexts == [
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
    result = await tool.aexecute({"query": "python"})
    assert isinstance(result, ToolResult)
    content, artifact = result

    assert "Title: Python 3.14 Docs" in content
    assert "URL: https://docs.python.org" in content
    assert "Content: Official Python documentation." in content
    assert artifact.source_used == "web_search"
    assert artifact.citations == [Citation(title="Python 3.14 Docs", url="https://docs.python.org", type="web")]
    assert artifact.retrieved_contexts == [
        "Title: Python 3.14 Docs\nURL: https://docs.python.org\nContent: Official Python documentation."
    ]
    mock_search.ainvoke.assert_called_once()


def test_create_web_search_tool_empty():
    mock_search = MagicMock()
    mock_search.invoke.return_value = {"results": []}

    tool = create_web_search_tool(search_client=mock_search)
    result = tool.execute({"query": "nonexistent"})
    assert isinstance(result, ToolResult)
    content, artifact = result

    assert content == "No relevant web search results found."
    assert artifact.citations == []
    assert artifact.retrieved_contexts == []
    assert artifact.source_used == "web_search"


def test_create_web_search_tool_string_output():
    mock_search = MagicMock()
    mock_search.invoke.return_value = "Direct raw string search result."

    tool = create_web_search_tool(search_client=mock_search)
    result = tool.execute({"query": "raw"})
    assert isinstance(result, ToolResult)
    content, artifact = result

    assert content == "Direct raw string search result."
    assert artifact.citations == []
    assert artifact.retrieved_contexts == ["Direct raw string search result."]
    assert artifact.source_used == "web_search"


def test_create_web_search_tool_sync_exception_propagation():
    mock_search = MagicMock()
    mock_search.invoke.side_effect = RuntimeError("Tavily API rate limit exceeded")

    tool = create_web_search_tool(search_client=mock_search)
    with pytest.raises(RuntimeError, match="Tavily API rate limit exceeded"):
        tool.execute({"query": "rate_limited"})


@pytest.mark.asyncio
async def test_create_web_search_tool_async_exception_propagation():
    mock_search = MagicMock()
    mock_search.ainvoke = AsyncMock(side_effect=RuntimeError("Tavily API rate limit exceeded"))

    tool = create_web_search_tool(search_client=mock_search)
    with pytest.raises(RuntimeError, match="Tavily API rate limit exceeded"):
        await tool.aexecute({"query": "rate_limited"})


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
    content, artifact = tool.execute({"query": "laptop replacement"})

    assert "Hardware policy chunk." in content
    assert artifact.citations == [Citation(title="hardware.md", url="", type="private_kb")]
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
    content, artifact = tool.execute({"query": "policy"})
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
    content, artifact = tool.execute({"query": "test query"})
    assert "Clean search answer" in content
    assert artifact.citations == [Citation(title="Web Title", url="https://example.com", type="web")]


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


def test_tool_evidence_artifact_model_attributes():
    # Defaults
    art_default = ToolEvidenceArtifact()
    assert art_default.citations == []
    assert art_default.retrieved_contexts == []
    assert art_default.source_used == "direct"
    assert art_default.raw_payload is None

    # Typed attributes
    citation = Citation(title="vpn.md", url="", type="private_kb")
    art = ToolEvidenceArtifact(
        citations=[citation],
        retrieved_contexts=["VPN connection steps"],
        source_used="vectorstore",
        raw_payload={"custom": "doc_payload"},
    )

    assert art.citations == [citation]
    assert art.retrieved_contexts == ["VPN connection steps"]
    assert art.source_used == "vectorstore"
    assert art.raw_payload == {"custom": "doc_payload"}

    # Subscripting is not supported on pure domain model (Zero-Shim standard)
    with pytest.raises(TypeError):
        _ = art["citations"]  # type: ignore[index]


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


# ============================================================================
# Ticket 02: RAGTool, rag_tool, FakeRAGTool, and Concrete Adapter Tests
# ============================================================================


def test_rag_tool_sync_execute_and_unpacking():
    def dummy_run(query: str) -> tuple[str, ToolEvidenceArtifact]:
        art = ToolEvidenceArtifact(
            citations=[Citation(title="Doc 1", url="https://doc1.com")],
            retrieved_contexts=["Context 1"],
            source_used="vectorstore",
        )
        return "Formatted response", art

    tool = rag_tool(
        func=dummy_run,
        name="test_tool",
        description="A test tool",
        args_schema=KnowledgeSearchInput,
    )
    assert isinstance(tool, RAGTool)

    # 1. Execute with dict
    res1 = tool.execute({"query": "vpn setup"})
    assert isinstance(res1, ToolResult)
    assert res1.content == "Formatted response"
    assert res1.artifact.source_used == "vectorstore"
    assert len(res1.artifact.citations) == 1
    assert res1.artifact.citations[0].title == "Doc 1"

    # 2-tuple unpacking backward compatibility
    content, artifact = res1
    assert content == "Formatted response"
    assert artifact.source_used == "vectorstore"

    # 2. Execute with BaseModel
    res2 = tool.execute(KnowledgeSearchInput(query="vpn setup"))
    assert res2.content == "Formatted response"

    # 3. Execute with plain string
    res3 = tool.execute("vpn setup")
    assert res3.content == "Formatted response"


@pytest.mark.asyncio
async def test_rag_tool_async_aexecute():
    async def dummy_arun(query: str) -> tuple[str, ToolEvidenceArtifact]:
        art = ToolEvidenceArtifact(
            citations=[Citation(title="Web Result", url="https://web.com")],
            retrieved_contexts=["Web context"],
            source_used="web_search",
        )
        return "Web response", art

    tool = rag_tool(
        coroutine=dummy_arun,
        name="async_tool",
        description="An async test tool",
        args_schema=WebSearchInput,
    )

    res = await tool.aexecute({"query": "fastapi"})
    assert isinstance(res, ToolResult)
    assert res.content == "Web response"
    assert res.artifact.source_used == "web_search"
    assert len(res.artifact.citations) == 1


def test_rag_tool_config_injection_without_monkeypatch():
    captured_config = None

    def dummy_run_with_config(query: str, config: RunnableConfig | None = None) -> tuple[str, ToolEvidenceArtifact]:
        nonlocal captured_config
        captured_config = config
        return "Config response", ToolEvidenceArtifact(source_used="vectorstore")

    tool = rag_tool(
        func=dummy_run_with_config,
        name="config_tool",
        description="A config test tool",
        args_schema=KnowledgeSearchInput,
    )

    res = tool.execute({"query": "test"}, config={"configurable": {"thread_id": "123"}})
    assert res.content == "Config response"
    assert captured_config is not None
    assert captured_config.get("configurable", {}).get("thread_id") == "123"


def test_rag_tool_invoke_produces_tool_message_with_artifact():
    def dummy_run(query: str) -> tuple[str, ToolEvidenceArtifact]:
        art = ToolEvidenceArtifact(
            citations=[Citation(title="Doc A")],
            retrieved_contexts=["Snippet A"],
            source_used="vectorstore",
        )
        return "Agent display text", art

    tool = rag_tool(
        func=dummy_run,
        name="agent_kb_tool",
        description="Tool for agent",
        args_schema=KnowledgeSearchInput,
    )

    tool_call = {
        "name": "agent_kb_tool",
        "args": {"query": "search query"},
        "id": "call_abc_456",
        "type": "tool_call",
    }
    msg = tool.invoke(tool_call)
    assert isinstance(msg, ToolMessage)
    assert msg.content == "Agent display text"
    assert msg.tool_call_id == "call_abc_456"
    assert isinstance(msg.artifact, ToolEvidenceArtifact)
    assert msg.artifact.source_used == "vectorstore"
    assert len(msg.artifact.citations) == 1
    assert msg.artifact.citations[0].title == "Doc A"


def test_fake_rag_tool_recording_and_returns():
    art = ToolEvidenceArtifact(
        citations=[Citation(title="Fake Citation", url="https://fake.com")],
        retrieved_contexts=["Fake snippet"],
        source_used="fake_source",
    )
    fake = FakeRAGTool(
        name="fake_tool",
        description="Fake test tool",
        content="Fake static output",
        artifact=art,
    )

    # Direct sync execution
    res1 = fake.execute({"query": "hello"}, config={"tags": ["t1"]})
    assert isinstance(res1, ToolResult)
    assert res1.content == "Fake static output"
    assert res1.artifact == art
    assert len(fake.execute_calls) == 1
    assert fake.execute_calls[0]["input"] == {"query": "hello"}
    assert fake.execute_calls[0]["config"] == {"tags": ["t1"]}

    # Direct unpacking
    c, a = res1
    assert c == "Fake static output"
    assert a == art


@pytest.mark.asyncio
async def test_fake_rag_tool_async_recording_and_invoke():
    art = ToolEvidenceArtifact(
        citations=[Citation(title="Fake Citation")],
        source_used="fake_async",
    )
    fake = FakeRAGTool(
        name="fake_tool_async",
        content="Async static content",
        artifact=art,
    )

    # Async execute
    res = await fake.aexecute("my query string", config={"tags": ["t2"]})
    assert isinstance(res, ToolResult)
    assert res.content == "Async static content"
    assert len(fake.aexecute_calls) == 1
    assert fake.aexecute_calls[0]["input"] == "my query string"

    # Agent graph invoke produces ToolMessage with artifact
    tool_call = {
        "name": "fake_tool_async",
        "args": {"query": "agent call"},
        "id": "call_fake_123",
        "type": "tool_call",
    }
    msg = await fake.ainvoke(tool_call)
    assert isinstance(msg, ToolMessage)
    assert msg.content == "Async static content"
    assert msg.artifact == art
    assert len(fake.aexecute_calls) == 2
    assert fake.aexecute_calls[1]["input"] == {"query": "agent call"}


@pytest.mark.asyncio
async def test_fake_rag_tool_tool_call_invocation_records_calls():
    art = ToolEvidenceArtifact(
        citations=[Citation(title="Doc 1", url="https://doc1.com")],
        retrieved_contexts=["Snippet 1"],
        source_used="fake_source",
    )
    fake = FakeRAGTool(
        name="fake_agent_tool",
        content="Static agent response",
        artifact=art,
    )

    # 1. Sync invoke with tool call dictionary
    sync_tool_call = {
        "name": "fake_agent_tool",
        "args": {"query": "sync search"},
        "id": "call_sync_1",
        "type": "tool_call",
    }
    sync_msg = fake.invoke(sync_tool_call, config={"tags": ["tag-sync"]})
    assert isinstance(sync_msg, ToolMessage)
    assert sync_msg.content == "Static agent response"
    assert sync_msg.artifact == art
    assert len(fake.execute_calls) == 1
    assert fake.execute_calls[0]["input"] == {"query": "sync search"}

    # 2. Async invoke with tool call dictionary
    async_tool_call = {
        "name": "fake_agent_tool",
        "args": {"query": "async search"},
        "id": "call_async_2",
        "type": "tool_call",
    }
    async_msg = await fake.ainvoke(async_tool_call, config={"tags": ["tag-async"]})
    assert isinstance(async_msg, ToolMessage)
    assert async_msg.content == "Static agent response"
    assert async_msg.artifact == art
    assert len(fake.aexecute_calls) == 1
    assert fake.aexecute_calls[0]["input"] == {"query": "async search"}


def test_concrete_tools_pure_config_injection():
    # 1. Retriever tool receives config natively
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = [Document(page_content="Config chunk", metadata={"source": "conf.md"})]
    kb_tool = create_retriever_tool(retriever=mock_retriever)
    config_kb = cast(RunnableConfig, {"configurable": {"session_id": "kb-123"}})
    res_kb = kb_tool.execute({"query": "conf"}, config=config_kb)
    assert res_kb.artifact.source_used == "vectorstore"
    mock_retriever.invoke.assert_called_once_with("conf", config=config_kb)

    # 2. Web search tool receives config natively
    mock_search = MagicMock()
    mock_search.invoke.return_value = {"results": [{"title": "Web Conf", "url": "https://conf.com", "content": "text"}]}
    web_tool = create_web_search_tool(search_client=mock_search)
    config_web = cast(RunnableConfig, {"configurable": {"session_id": "web-456"}})
    res_web = web_tool.execute({"query": "web"}, config=config_web)
    assert res_web.artifact.source_used == "web_search"
    mock_search.invoke.assert_called_once_with({"query": "web"}, config=config_web)


@pytest.mark.asyncio
async def test_concrete_tools_agent_invoke_produces_tool_message_with_artifact():
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = [Document(page_content="Agent KB doc", metadata={"source": "agent.md"})]
    kb_tool = create_retriever_tool(retriever=mock_retriever)

    tool_call = {
        "name": "knowledge_base_search",
        "args": {"query": "agent query"},
        "id": "call_kb_999",
        "type": "tool_call",
    }
    msg = kb_tool.invoke(tool_call)
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_kb_999"
    assert isinstance(msg.artifact, ToolEvidenceArtifact)
    assert msg.artifact.source_used == "vectorstore"
    assert len(msg.artifact.citations) == 1
    assert msg.artifact.citations[0].title == "agent.md"


def test_zero_shim_pruning_and_clean_exports():
    import app.rag.tools as tools_pkg
    import app.rag.tools.base as base_mod

    # Obsolete shims must be completely deleted
    assert not hasattr(base_mod, "DualOutputStructuredTool")
    assert not hasattr(base_mod, "_TOOL_INVOKE_CONFIG")
    assert not hasattr(base_mod, "_UNSET")
    assert not hasattr(base_mod, "get_invoke_config")
    assert not hasattr(tools_pkg, "DualOutputStructuredTool")
    assert not hasattr(tools_pkg, "_TOOL_INVOKE_CONFIG")
    assert not hasattr(tools_pkg, "_UNSET")
    assert not hasattr(tools_pkg, "get_invoke_config")

    # Canonical 12 exports
    expected_exports = {
        "RAGTool",
        "rag_tool",
        "FakeRAGTool",
        "Citation",
        "ToolEvidenceArtifact",
        "ToolResult",
        "AggregatedEvidence",
        "aggregate_tool_evidence",
        "create_retriever_tool",
        "create_web_search_tool",
        "KnowledgeSearchInput",
        "WebSearchInput",
    }
    assert set(tools_pkg.__all__) == expected_exports
    for name in expected_exports:
        assert hasattr(tools_pkg, name)
