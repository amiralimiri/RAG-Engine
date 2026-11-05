import functools
import inspect
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.rag.classifiers import FakeClassifierAdapter
from app.rag.engine import (
    GENERATION_NODES,
    create_agentic_rag_graph,
    direct_answer,
    get_chat_model,
)
from app.rag.prompts import LocalPromptStore
from app.rag.tools import Citation, ToolEvidenceArtifact, ToolResult, create_retriever_tool, create_web_search_tool


@pytest.fixture
def mock_llm():
    mock_llm_inst = MagicMock(spec=BaseChatModel)

    async def mock_ainvoke(prompt, **kwargs):
        prompt_str = str(prompt)
        if "search quer" in prompt_str.lower() or "rewrite" in prompt_str.lower():
            return AIMessage(content="rewritten query text")
        return AIMessage(content="Generated response")

    mock_llm_inst.ainvoke = AsyncMock(side_effect=mock_ainvoke)
    mock_llm_inst.invoke = MagicMock(return_value=AIMessage(content="rewritten query text"))
    return mock_llm_inst


@pytest.fixture
def fake_retriever():
    mock_ret = MagicMock()
    mock_ret.invoke.return_value = [
        Document(
            page_content="Private company VPN policy details",
            metadata={"source": "docs/it/vpn_policy.md"},
        )
    ]
    return mock_ret


@pytest.fixture
def mock_kb_tool(fake_retriever):
    return create_retriever_tool(retriever=fake_retriever)


@pytest.fixture
def mock_web_client():
    mock_client = MagicMock()
    mock_client.invoke.return_value = {
        "answer": "Web answer content",
        "results": [
            {
                "title": "Support Guide",
                "url": "https://example.com/guide",
                "content": "Detailed guide snippet",
            }
        ],
    }
    return mock_client


@pytest.fixture
def mock_web_tool(mock_web_client):
    return create_web_search_tool(search_client=mock_web_client)


def test_generation_nodes_set():
    assert GENERATION_NODES == {"generate_from_kb", "generate_from_web", "direct_answer"}


def test_get_chat_model_returns_chat_openai(monkeypatch):
    from langchain_openai import ChatOpenAI

    model = get_chat_model()
    assert isinstance(model, ChatOpenAI)
    assert model.temperature == 0.0


def test_graph_creation_topology():
    classifier = FakeClassifierAdapter()
    graph = create_agentic_rag_graph(classifier)
    expected_nodes = {
        "route_question",
        "retrieve_kb",
        "grade_kb",
        "search_web",
        "grade_web",
        "rewrite_query",
        "generate_from_kb",
        "generate_from_web",
        "direct_answer",
        "insufficient",
    }
    for node_name in expected_nodes:
        assert node_name in graph.nodes


def test_branch_direct_answer(mock_llm, mock_kb_tool, mock_web_tool, fake_retriever):
    classifier = FakeClassifierAdapter(route_decision="direct")
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    result = graph.invoke({"question": "Hi, how are you?", "retry_count": 0})
    assert result["source_used"] == "direct"
    assert result["answer"] == "Generated response"
    assert result["retrieved_contexts"] == []
    assert any("Direct response" in t for t in result["trace"])
    assert fake_retriever.invoke.call_count == 0


def test_branch_kb_good(mock_llm, mock_kb_tool, mock_web_tool, fake_retriever):
    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    result = graph.invoke({"question": "How do I configure VPN?", "retry_count": 0})
    assert result["source_used"] == "vectorstore"
    assert result["answer"] == "Generated response"
    assert len(result["kb_docs"]) == 1
    assert "Private company VPN policy details" in result["retrieved_contexts"]
    assert any("PRIVATE KB" in t for t in result["trace"])
    assert fake_retriever.invoke.call_count == 1
    assert len(result["citations"]) == 1
    assert result["citations"][0]["title"] == "vpn_policy.md"


def test_branch_kb_weak_web_good(mock_llm, mock_kb_tool, mock_web_tool, mock_web_client):
    classifier = FakeClassifierAdapter(
        route_decision="kb",
        kb_grade="weak",
        web_grade="good",
    )
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    result = graph.invoke({"question": "Latest external driver patch?", "retry_count": 0})
    assert result["source_used"] == "web_search"
    assert result["answer"] == "Generated response"
    assert mock_web_client.invoke.call_count == 1
    assert any("Web fallback" in t for t in result["trace"])
    assert any("WEB SEARCH" in t for t in result["trace"])


def test_branch_retry_rewrite_then_kb_good(mock_llm, mock_kb_tool, mock_web_tool, fake_retriever):
    classifier = FakeClassifierAdapter(
        route_decision="kb",
        kb_grade=["weak", "good"],
        web_grade=["weak"],
    )
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    result = graph.invoke({"question": "Vague network error", "retry_count": 0})
    assert result["source_used"] == "vectorstore"
    assert result["retry_count"] == 1
    assert result["current_query"] == "rewritten query text"
    assert any("Query rewrite" in t for t in result["trace"])
    assert fake_retriever.invoke.call_count == 2


def test_branch_insufficient_evidence(mock_llm, mock_kb_tool, mock_web_tool, monkeypatch):
    from app.core.config import Settings

    monkeypatch.setattr("app.rag.engine.get_settings", lambda: Settings(max_retries=1, _env_file=None))

    classifier = FakeClassifierAdapter(
        route_decision="kb",
        kb_grade="weak",
        web_grade="weak",
    )
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    result = graph.invoke({"question": "Completely unknown topic", "retry_count": 0})
    assert result["source_used"] == "insufficient_evidence"
    assert "couldn't find enough reliable evidence" in result["answer"]
    assert any("insufficient reliable evidence" in t for t in result["trace"])
    assert result["retry_count"] >= 1


def test_retriever_default_hook(mock_llm):
    mock_default_retriever = MagicMock()
    mock_default_retriever.invoke.return_value = [Document(page_content="Default KB doc")]
    mock_store = MagicMock()
    mock_store.as_retriever.return_value = mock_default_retriever

    with patch("app.rag.knowledge.get_knowledge_store", return_value=mock_store):
        classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
        # kb_tool=None to test default retriever hook
        graph = create_agentic_rag_graph(classifier, model=mock_llm, kb_tool=None)
        res = graph.invoke({"question": "Test default retriever", "retry_count": 0})

        assert mock_default_retriever.invoke.call_count == 1
        assert res["retrieved_contexts"] == ["Default KB doc"]


def test_nodes_bind_contextvars_steps(mock_llm, mock_kb_tool, mock_web_tool):
    bound_steps: list[str] = []
    original_bind = structlog.contextvars.bind_contextvars

    def tracking_bind(**kwargs):
        if "step" in kwargs:
            bound_steps.append(kwargs["step"])
        return original_bind(**kwargs)

    with patch("structlog.contextvars.bind_contextvars", side_effect=tracking_bind):
        classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
        graph = create_agentic_rag_graph(
            classifier,
            model=mock_llm,
            kb_tool=mock_kb_tool,
            web_tool=mock_web_tool,
        )
        graph.invoke({"question": "Test steps", "retry_count": 0})

    assert "route_question" in bound_steps
    assert "retrieve_kb" in bound_steps
    assert "grade_kb" in bound_steps
    assert "generate_from_kb" in bound_steps


@pytest.mark.asyncio
async def test_create_agentic_rag_graph_accepts_injected_kb_and_web_tools(mock_llm):
    custom_kb_tool = MagicMock()
    custom_kb_doc = Document(page_content="Injected KB content", metadata={"source": "injected/kb.md"})
    custom_kb_tool.aexecute = AsyncMock(
        return_value=ToolResult(
            content="[Source: injected/kb.md]\nInjected KB content",
            artifact=ToolEvidenceArtifact(
                raw_payload=[custom_kb_doc],
                citations=[Citation(title="kb.md", url="", type="private_kb")],
                retrieved_contexts=["Injected KB content"],
                source_used="vectorstore",
            ),
        )
    )

    custom_web_tool = MagicMock()
    custom_web_tool.aexecute = AsyncMock(
        return_value=ToolResult(
            content="Web search snippet",
            artifact=ToolEvidenceArtifact(
                citations=[Citation(title="Web Title", url="https://example.com", type="web")],
                retrieved_contexts=["Title: Web Title\nURL: https://example.com\nContent: Web snippet"],
                source_used="web_search",
            ),
        )
    )

    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=custom_kb_tool,
        web_tool=custom_web_tool,
    )

    result = await graph.ainvoke({"question": "How to do something?", "retry_count": 0})
    custom_kb_tool.aexecute.assert_awaited_once()
    assert result["kb_docs"] == [custom_kb_doc]
    assert result["retrieved_contexts"] == ["Injected KB content"]
    assert result["citations"] == [{"title": "kb.md", "url": "", "type": "private_kb"}]


@pytest.mark.asyncio
async def test_create_agentic_rag_graph_injected_web_tool_fallback(mock_llm):
    custom_kb_tool = MagicMock()
    custom_kb_tool.aexecute = AsyncMock(
        return_value=ToolResult(
            content="No docs",
            artifact=ToolEvidenceArtifact(),
        )
    )

    custom_web_tool = MagicMock()
    custom_web_tool.aexecute = AsyncMock(
        return_value=ToolResult(
            content="Injected web snippet",
            artifact=ToolEvidenceArtifact(
                citations=[Citation(title="Custom Web", url="https://custom.com", type="web")],
                retrieved_contexts=["Title: Custom Web\nURL: https://custom.com\nContent: Injected web snippet"],
                source_used="web_search",
            ),
        )
    )

    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="weak", web_grade="good")
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=custom_kb_tool,
        web_tool=custom_web_tool,
    )

    result = await graph.ainvoke({"question": "Need external info", "retry_count": 0})
    custom_web_tool.aexecute.assert_awaited_once()
    assert result["source_used"] == "web_search"
    assert result["web_results"] == "Injected web snippet"
    assert result["citations"] == [{"title": "Custom Web", "url": "https://custom.com", "type": "web"}]
    assert result["retrieved_contexts"] == ["Title: Custom Web\nURL: https://custom.com\nContent: Injected web snippet"]


def test_graph_nodes_are_native_async_coroutines():
    classifier = FakeClassifierAdapter()
    graph = create_agentic_rag_graph(classifier)
    retrieve_kb_node = graph.nodes["retrieve_kb"]
    search_web_node = graph.nodes["search_web"]

    assert retrieve_kb_node.bound.func is None, "retrieve_kb must not have a sync fallback runner"
    assert not isinstance(retrieve_kb_node.bound.afunc, functools.partial), (
        "retrieve_kb must not be wrapped in run_in_executor"
    )
    assert inspect.iscoroutinefunction(retrieve_kb_node.bound.afunc), "retrieve_kb afunc must be a coroutine function"

    assert search_web_node.bound.func is None, "search_web must not have a sync fallback runner"
    assert not isinstance(search_web_node.bound.afunc, functools.partial), (
        "search_web must not be wrapped in run_in_executor"
    )
    assert inspect.iscoroutinefunction(search_web_node.bound.afunc), "search_web afunc must be a coroutine function"


@pytest.mark.asyncio
async def test_graph_async_streaming_events_retrieve_and_search(mock_llm):
    custom_kb_tool = MagicMock()
    custom_kb_doc = Document(page_content="Async streaming KB doc", metadata={"source": "streaming.md"})
    custom_kb_tool.aexecute = AsyncMock(
        return_value=ToolResult(
            content="[Source: streaming.md]\nAsync streaming KB doc",
            artifact=ToolEvidenceArtifact(
                raw_payload=[custom_kb_doc],
                citations=[Citation(title="streaming.md", url="", type="private_kb")],
            ),
        )
    )

    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
    graph = create_agentic_rag_graph(classifier, model=mock_llm, kb_tool=custom_kb_tool)

    events = []
    async for event in graph.astream_events({"question": "Streaming test", "retry_count": 0}, version="v2"):
        events.append(event)

    node_names = [
        e.get("metadata", {}).get("langgraph_node")
        for e in events
        if e.get("event") in ("on_chain_start", "on_chain_end")
    ]
    assert "retrieve_kb" in node_names


def test_to_tool_result_handles_empty_dict_artifact():
    from langchain_core.messages import ToolMessage

    from app.rag.tools.base import ToolEvidenceArtifact, _to_tool_result

    msg = ToolMessage(content="Found nothing", artifact={}, tool_call_id="call_123")
    result = _to_tool_result(msg)
    assert result.content == "Found nothing"
    assert isinstance(result.artifact, ToolEvidenceArtifact)


@pytest.mark.asyncio
async def test_direct_answer_accepts_injected_prompt_store(mock_llm):
    custom_store = LocalPromptStore(templates={"rag:direct_answer": "INJECTED DIRECT: {{question}}"})
    result = await direct_answer(
        cast(Any, {"question": "How are you?"}),
        config=cast(Any, {"tags": ["custom-test"]}),
        model=mock_llm,
        prompt_store=custom_store,
    )
    assert result["source_used"] == "direct"
    assert mock_llm.ainvoke.await_count == 1
    call_args = mock_llm.ainvoke.call_args
    prompt_arg = call_args[0][0]
    config_arg = call_args[1]["config"]
    assert prompt_arg == "INJECTED DIRECT: How are you?"
    assert config_arg["metadata"]["prompt_name"] == "rag:direct_answer"
    assert config_arg["tags"] == ["custom-test"]


@pytest.mark.asyncio
async def test_create_agentic_rag_graph_injected_prompt_store_direct_and_kb(mock_llm, mock_kb_tool):
    custom_store = LocalPromptStore(
        templates={
            "rag:direct_answer": "INJECTED DIRECT ROUTE: {{question}}",
            "rag:generate_kb": "INJECTED KB ROUTE: {{question}} || {{context}}",
            "rag:condense_query": "INJECTED CONDENSE: {{question}}",
        }
    )

    # 1. Direct answer flow with injected prompt store
    classifier_direct = FakeClassifierAdapter(route_decision="direct")
    graph_direct = create_agentic_rag_graph(
        classifier_direct,
        model=mock_llm,
        prompt_store=custom_store,
    )
    res_direct = await graph_direct.ainvoke({"question": "Hi IT support", "retry_count": 0})
    assert res_direct["source_used"] == "direct"
    last_call = mock_llm.ainvoke.call_args
    assert "INJECTED DIRECT ROUTE: Hi IT support" in last_call[0][0]
    assert last_call[1]["config"]["metadata"]["prompt_name"] == "rag:direct_answer"

    # 2. KB generation flow with injected prompt store
    classifier_kb = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
    graph_kb = create_agentic_rag_graph(
        classifier_kb,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        prompt_store=custom_store,
    )
    res_kb = await graph_kb.ainvoke({"question": "VPN setup details", "retry_count": 0})
    assert res_kb["source_used"] == "vectorstore"
    last_call_kb = mock_llm.ainvoke.call_args
    assert "INJECTED KB ROUTE: VPN setup details" in last_call_kb[0][0]
    assert last_call_kb[1]["config"]["metadata"]["prompt_name"] == "rag:generate_kb"

    # 3. Condense query flow with injected prompt store
    res_condense = await graph_direct.ainvoke(
        {
            "question": "Can you elaborate?",
            "retry_count": 0,
            "messages": [
                HumanMessage(content="Previous question"),
                AIMessage(content="Previous answer"),
                HumanMessage(content="Can you elaborate?"),
            ],
        }
    )
    assert res_condense["source_used"] == "direct"
    # Find the condense_query call among the invocations
    all_prompts = [call[0][0] for call in mock_llm.ainvoke.call_args_list]
    assert any("INJECTED CONDENSE: Can you elaborate?" in p for p in all_prompts)


@pytest.mark.asyncio
async def test_create_agentic_rag_graph_injected_prompt_store_rewrite_and_web(mock_llm, mock_kb_tool, mock_web_tool):
    custom_store = LocalPromptStore(
        templates={
            "rag:rewrite_query": "INJECTED REWRITE: {{question}} / {{current_query}}",
            "rag:generate_web": "INJECTED WEB ROUTE: {{question}} || {{web_results}}",
            "rag:generate_kb": "INJECTED KB ROUTE: {{question}}",
        }
    )

    # Web generation flow with injected prompt store
    classifier_web = FakeClassifierAdapter(route_decision="kb", kb_grade="weak", web_grade="good")
    graph_web = create_agentic_rag_graph(
        classifier_web,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
        prompt_store=custom_store,
    )
    res_web = await graph_web.ainvoke({"question": "External query", "retry_count": 0})
    assert res_web["source_used"] == "web_search"
    all_prompts = [call[0][0] for call in mock_llm.ainvoke.call_args_list]
    assert any("INJECTED WEB ROUTE: External query" in p for p in all_prompts)

    # Query rewrite flow with injected prompt store
    classifier_rewrite = FakeClassifierAdapter(
        route_decision="kb",
        kb_grade=["weak", "good"],
        web_grade="weak",
    )
    graph_rewrite = create_agentic_rag_graph(
        classifier_rewrite,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
        prompt_store=custom_store,
    )
    await graph_rewrite.ainvoke({"question": "Tough question", "retry_count": 0})
    all_prompts_rewrite = [call[0][0] for call in mock_llm.ainvoke.call_args_list]
    assert any("INJECTED REWRITE: Tough question" in p for p in all_prompts_rewrite)
