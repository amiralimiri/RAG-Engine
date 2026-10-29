from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
import structlog

from app.rag.classifiers import FakeClassifierAdapter
from app.rag.engine import (
    GENERATION_NODES,
    ask,
    create_agentic_rag_graph,
    register_workflow_graph,
)
from app.rag.state import EvidenceGrade, RouteDecision


@pytest.fixture
def mock_llm_and_web():
    mock_llm_inst = MagicMock()
    mock_llm_inst.ainvoke = AsyncMock(return_value=AIMessage(content="Generated response"))
    mock_llm_inst.invoke = MagicMock(return_value=AIMessage(content="rewritten query text"))

    mock_web_tool_inst = MagicMock()
    mock_web_tool_inst.invoke.return_value = {
        "answer": "Web answer content",
        "results": [
            {
                "title": "Support Guide",
                "url": "https://example.com/guide",
                "content": "Detailed guide snippet",
            }
        ],
    }

    with (
        patch("app.rag.engine.llm", return_value=mock_llm_inst),
        patch("app.rag.engine.web_search_tool", return_value=mock_web_tool_inst),
    ):
        yield {
            "llm": mock_llm_inst,
            "web_search": mock_web_tool_inst,
        }


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


def test_generation_nodes_set():
    assert GENERATION_NODES == {"generate_from_kb", "generate_from_web", "direct_answer"}


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
    # StateGraph nodes or compiled graph nodes
    for node_name in expected_nodes:
        assert node_name in graph.nodes


def test_branch_direct_answer(mock_llm_and_web, fake_retriever):
    classifier = FakeClassifierAdapter(route_decision="direct")
    graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)

    result = graph.invoke({"question": "Hi, how are you?", "retry_count": 0})
    assert result["source_used"] == "direct"
    assert result["answer"] == "Generated response"
    assert result["retrieved_contexts"] == []
    assert any("Direct response" in t for t in result["trace"])
    assert fake_retriever.invoke.call_count == 0


def test_branch_kb_good(mock_llm_and_web, fake_retriever):
    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
    graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)

    result = graph.invoke({"question": "How do I configure VPN?", "retry_count": 0})
    assert result["source_used"] == "vectorstore"
    assert result["answer"] == "Generated response"
    assert len(result["kb_docs"]) == 1
    assert "Private company VPN policy details" in result["retrieved_contexts"]
    assert any("PRIVATE KB" in t for t in result["trace"])
    assert fake_retriever.invoke.call_count == 1
    assert len(result["citations"]) == 1
    assert result["citations"][0]["title"] == "vpn_policy.md"


def test_branch_kb_weak_web_good(mock_llm_and_web, fake_retriever):
    classifier = FakeClassifierAdapter(
        route_decision="kb",
        kb_grade="weak",
        web_grade="good",
    )
    graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)

    result = graph.invoke({"question": "Latest external driver patch?", "retry_count": 0})
    assert result["source_used"] == "web_search"
    assert result["answer"] == "Generated response"
    assert mock_llm_and_web["web_search"].invoke.call_count == 1
    assert any("Web fallback" in t for t in result["trace"])
    assert any("WEB SEARCH" in t for t in result["trace"])


def test_branch_retry_rewrite_then_kb_good(mock_llm_and_web, fake_retriever):
    classifier = FakeClassifierAdapter(
        route_decision="kb",
        kb_grade=["weak", "good"],
        web_grade=["weak"],
    )
    graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)

    result = graph.invoke({"question": "Vague network error", "retry_count": 0})
    assert result["source_used"] == "vectorstore"
    assert result["retry_count"] == 1
    assert result["current_query"] == "rewritten query text"
    assert any("Query rewrite" in t for t in result["trace"])
    assert fake_retriever.invoke.call_count == 2


def test_branch_insufficient_evidence(mock_llm_and_web, fake_retriever, monkeypatch):
    from app.core.config import Settings
    monkeypatch.setattr("app.rag.engine.get_settings", lambda: Settings(max_retries=1, _env_file=None))

    classifier = FakeClassifierAdapter(
        route_decision="kb",
        kb_grade="weak",
        web_grade="weak",
    )
    graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)

    result = graph.invoke({"question": "Completely unknown topic", "retry_count": 0})
    assert result["source_used"] == "insufficient_evidence"
    assert "couldn't find enough reliable evidence" in result["answer"]
    assert any("insufficient reliable evidence" in t for t in result["trace"])
    assert result["retry_count"] >= 1


def test_retriever_default_hook(mock_llm_and_web):
    mock_default_retriever = MagicMock()
    mock_default_retriever.invoke.return_value = [Document(page_content="Default KB doc")]

    with patch("app.rag.engine.get_retriever", return_value=mock_default_retriever):
        classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
        # retriever=None to test default
        graph = create_agentic_rag_graph(classifier, retriever=None)
        res = graph.invoke({"question": "Test default retriever", "retry_count": 0})

        assert mock_default_retriever.invoke.call_count == 1
        assert res["retrieved_contexts"] == ["Default KB doc"]


def test_nodes_bind_contextvars_steps(mock_llm_and_web, fake_retriever):
    bound_steps: list[str] = []
    original_bind = structlog.contextvars.bind_contextvars

    def tracking_bind(**kwargs):
        if "step" in kwargs:
            bound_steps.append(kwargs["step"])
        return original_bind(**kwargs)

    with patch("structlog.contextvars.bind_contextvars", side_effect=tracking_bind):
        classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
        graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)
        graph.invoke({"question": "Test steps", "retry_count": 0})

    assert "route_question" in bound_steps
    assert "retrieve_kb" in bound_steps
    assert "grade_kb" in bound_steps
    assert "generate_from_kb" in bound_steps


def test_unified_ask_interface(mock_llm_and_web, fake_retriever):
    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
    custom_graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)

    dummy_callback = MagicMock()
    dummy_callback.last_trace_id = "test-trace-12345"

    result = ask(
        "How do I setup email?",
        workflow="custom_workflow",
        session_id="session-42",
        user_id="user-99",
        metadata={"trace_id": "meta-trace-99"},
        callback=dummy_callback,
        graph=custom_graph,
    )

    assert isinstance(result, dict)
    assert result["source_used"] == "vectorstore"
    assert "retrieved_contexts" in result
    assert "citations" in result
    assert "trace" in result
    assert "answer" in result
    assert result.get("trace_id") in {"test-trace-12345", "meta-trace-99"}


def test_ask_with_registered_graph(mock_llm_and_web, fake_retriever):
    classifier = FakeClassifierAdapter(route_decision="direct")
    reg_graph = create_agentic_rag_graph(classifier, retriever=fake_retriever)
    register_workflow_graph("registered_test", reg_graph)

    result = ask("Hello registry", workflow="registered_test")
    assert result["source_used"] == "direct"
    assert result["answer"] == "Generated response"
