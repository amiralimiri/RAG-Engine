from typing import NotRequired, get_args, get_origin
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document

from app.api.schemas import ChatRequest
from app.rag import engine
from app.rag.engine import create_agentic_rag_graph
from app.rag.state import AgentState
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.runner import WorkflowRunner
from app.services.prompts import FallbackPrompt


@pytest.fixture(autouse=True)
def mock_workflow_prompts():
    """Ensure all prompt lookups return local fallback prompts offline without Langfuse calls."""
    with patch("app.rag.engine.get_workflow_prompt") as mock_get:
        mock_get.side_effect = lambda name: FallbackPrompt(name, f"Prompt template for {name}: {{{{question}}}}")
        yield mock_get


# ==============================================================================
# 1. State Schema Verification
# ==============================================================================

def test_agent_state_defines_retrieved_contexts():
    """Verify AgentState in app/rag/state.py explicitly defines retrieved_contexts: list[str]."""
    assert "retrieved_contexts" in AgentState.__annotations__, (
        "AgentState must define 'retrieved_contexts' in its type annotations"
    )
    anno = AgentState.__annotations__["retrieved_contexts"]

    # Unwrap NotRequired if present
    if get_origin(anno) is NotRequired:
        args = get_args(anno)
        inner = args[0] if args else anno
    else:
        inner = anno

    origin = get_origin(inner) or inner
    assert origin in (list, list[str]), f"Expected list origin, got {origin}"
    inner_args = get_args(inner)
    assert inner_args == (str,), f"Expected (str,) args for list[str], got {inner_args}"


# ==============================================================================
# 2. Pure Graph Topology Context Population via create_agentic_rag_graph
# ==============================================================================

def test_graph_kb_retrieval_and_generation_populates_retrieved_contexts():
    """KB retrieval path populates retrieved_contexts with raw chunk strings and generates from KB."""
    docs = [
        Document(page_content="Chunk 1: VPN guide.", metadata={"source": "kb/vpn.md"}),
        Document(page_content="Chunk 2: Troubleshooting.", metadata={"source": "kb/vpn.md"}),
    ]

    mock_classifier = MagicMock()
    mock_classifier.route.return_value = MagicMock(route="kb")
    mock_classifier.grade_kb.return_value = MagicMock(grade="good")

    mock_kb_tool = MagicMock()
    mock_kb_tool.ainvoke = AsyncMock(return_value=(
        "Formatted KB docs",
        {"documents": docs, "citations": [{"title": "vpn.md", "url": "", "type": "private_kb"}]},
    ))

    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=MagicMock(content="Follow these VPN steps."))

    graph = create_agentic_rag_graph(
        classifier=mock_classifier,
        model=mock_model,
        kb_tool=mock_kb_tool,
    )

    state: AgentState = {
        "question": "How to connect to VPN?",
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    }

    result = graph.invoke(state)
    assert "retrieved_contexts" in result, "Graph execution must preserve 'retrieved_contexts'"
    assert result["retrieved_contexts"] == [
        "Chunk 1: VPN guide.",
        "Chunk 2: Troubleshooting.",
    ]
    assert result["source_used"] == "vectorstore"
    assert result["answer"] == "Follow these VPN steps."


def test_graph_web_search_and_generation_populates_retrieved_contexts():
    """Web search path overwrites prior KB contexts with only the web contexts."""
    mock_classifier = MagicMock()
    mock_classifier.route.return_value = MagicMock(route="kb")
    mock_classifier.grade_kb.return_value = MagicMock(grade="weak")
    mock_classifier.grade_web.return_value = MagicMock(grade="good")

    mock_kb_tool = MagicMock()
    mock_kb_tool.ainvoke = AsyncMock(return_value=(
        "Stale KB chunk",
        {"documents": [Document(page_content="Prior rejected KB chunk", metadata={})], "citations": []},
    ))

    web_snippet = "Title: Vendor Support\nURL: https://example.com/vpn\nContent: Official Cisco AnyConnect docs"
    mock_web_tool = MagicMock()
    mock_web_tool.ainvoke = AsyncMock(return_value=(
        "External answer",
        {
            "retrieved_contexts": [web_snippet],
            "citations": [{"title": "Vendor Support", "url": "https://example.com/vpn", "type": "web"}],
        },
    ))

    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=MagicMock(content="Vendor recommends reinstalling."))

    graph = create_agentic_rag_graph(
        classifier=mock_classifier,
        model=mock_model,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    state: AgentState = {
        "question": "How to connect to VPN?",
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    }

    result = graph.invoke(state)
    assert "retrieved_contexts" in result
    assert result["retrieved_contexts"] == [web_snippet], (
        "web search must overwrite prior rejected KB contexts with only the web contexts"
    )
    assert result["source_used"] == "web_search"
    assert result["answer"] == "Vendor recommends reinstalling."


@pytest.mark.asyncio
async def test_direct_answer_returns_empty_retrieved_contexts():
    """direct_answer in engine returns empty list for retrieved_contexts."""
    mock_response = MagicMock(content="Hello! How can I help you today?")
    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    state: AgentState = {
        "question": "hello",
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "direct",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    }

    result = await engine.direct_answer(state, model=mock_model)
    assert "retrieved_contexts" in result, "direct_answer must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == []
    assert result["source_used"] == "direct"


def test_graph_direct_answer_path_returns_empty_retrieved_contexts():
    """Graph direct_answer path returns empty list for retrieved_contexts."""
    mock_classifier = MagicMock()
    mock_classifier.route.return_value = MagicMock(route="direct")

    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=MagicMock(content="Hello! How can I help you today?"))

    graph = create_agentic_rag_graph(
        classifier=mock_classifier,
        model=mock_model,
    )

    state: AgentState = {
        "question": "hello",
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    }

    result = graph.invoke(state)
    assert result["retrieved_contexts"] == []
    assert result["source_used"] == "direct"
    assert result["answer"] == "Hello! How can I help you today?"


def test_insufficient_returns_empty_retrieved_contexts():
    """insufficient in engine returns empty list for retrieved_contexts."""
    state: AgentState = {
        "question": "obscure question",
        "current_query": "obscure query",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "weak",
        "web_grade": "weak",
        "answer": "",
        "source_used": "",
        "retry_count": 2,
        "trace": [],
        "citations": [],
        "retrieved_contexts": ["stale weak chunk"],
    }

    result = engine.insufficient(state)
    assert "retrieved_contexts" in result, "insufficient must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == []
    assert result["source_used"] == "insufficient_evidence"


def test_graph_insufficient_path_returns_empty_retrieved_contexts(monkeypatch):
    """Graph insufficient path returns empty list for retrieved_contexts when retries exhausted."""
    mock_classifier = MagicMock()
    mock_classifier.route.return_value = MagicMock(route="kb")
    mock_classifier.grade_kb.return_value = MagicMock(grade="weak")
    mock_classifier.grade_web.return_value = MagicMock(grade="weak")

    mock_kb_tool = MagicMock()
    mock_kb_tool.ainvoke = AsyncMock(return_value=("No docs", {"documents": [], "citations": []}))

    mock_web_tool = MagicMock()
    mock_web_tool.ainvoke = AsyncMock(return_value=("No results", {"retrieved_contexts": [], "citations": []}))

    graph = create_agentic_rag_graph(
        classifier=mock_classifier,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    state: AgentState = {
        "question": "obscure question",
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 2,  # equals settings.max_retries
        "trace": [],
        "citations": [],
        "retrieved_contexts": ["stale weak chunk"],
    }

    result = graph.invoke(state)
    assert result["retrieved_contexts"] == []
    assert result["source_used"] == "insufficient_evidence"
    assert "reliable evidence" in result["answer"]
    assert "Stopped → insufficient reliable evidence" in result["trace"]


# ==============================================================================
# 3. WorkflowDefinition and Runner Initial State Contract Tests
# ==============================================================================

@pytest.mark.parametrize("workflow_id", ["fix_single_llm", "fix_single_jev"])
def test_workflow_initial_state_contains_retrieved_contexts(workflow_id: str):
    """WorkflowDefinition.to_initial_state initializes retrieved_contexts as []."""
    registry = get_workflow_registry()
    wf_def = registry.get(workflow_id)

    initial_state = wf_def.to_initial_state(ChatRequest(question="hello", workflow=workflow_id))
    assert "retrieved_contexts" in initial_state
    assert initial_state["retrieved_contexts"] == []


@pytest.mark.parametrize("workflow_id", ["fix_single_llm", "fix_single_jev"])
def test_workflow_runner_extracts_eval_context_with_retrieved_contexts(workflow_id: str):
    """WorkflowRunner execution extracts retrieved_contexts via wf_def.extract_eval_context."""
    registry = get_workflow_registry()
    wf_def = registry.get(workflow_id)

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "answer": "Answer with contexts",
        "source_used": "vectorstore",
        "trace": ["step1"],
        "citations": [],
        "retrieved_contexts": ["Context chunk A", "Context chunk B"],
        "current_query": "hello",
    }
    wf_def._compiled_graph = mock_graph

    runner = WorkflowRunner(registry=registry)
    chat_resp, final_state = runner._execute_with_state(ChatRequest(question="hello", workflow=workflow_id))

    assert chat_resp.answer == "Answer with contexts"
    eval_ctx = wf_def.extract_eval_context(final_state)
    assert eval_ctx.retrieved_contexts == ["Context chunk A", "Context chunk B"]
    assert eval_ctx.source_used == "vectorstore"
