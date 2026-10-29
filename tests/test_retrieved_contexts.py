import ast
from pathlib import Path
from typing import get_args, get_origin
from typing_extensions import NotRequired
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.documents import Document

from app.rag import fix_single_llm, fix_single_jev, engine
from app.rag.state import AgentState


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
# 2. fix_single_llm Node Tests
# ==============================================================================

def test_retrieve_kb_populates_retrieved_contexts_llm(monkeypatch):
    """retrieve_kb in engine populates retrieved_contexts with raw chunk strings."""
    docs = [
        Document(page_content="Chunk 1: VPN guide.", metadata={"source": "kb/vpn.md"}),
        Document(page_content="Chunk 2: Troubleshooting.", metadata={"source": "kb/vpn.md"}),
    ]
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = docs
    monkeypatch.setattr(engine, "get_retriever", lambda: mock_retriever)

    state: AgentState = {
        "question": "How to connect to VPN?",
        "current_query": "VPN connection instructions",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
    }

    result = engine.retrieve_kb(state)
    assert "retrieved_contexts" in result, "retrieve_kb must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == [
        "Chunk 1: VPN guide.",
        "Chunk 2: Troubleshooting.",
    ]


def test_search_web_appends_retrieved_contexts_llm(monkeypatch):
    """search_web in engine appends web search snippets to existing retrieved_contexts."""
    mock_tool = MagicMock()
    mock_tool.invoke.return_value = {
        "answer": "External answer",
        "results": [
            {
                "title": "Vendor Support",
                "url": "https://example.com/vpn",
                "content": "Official Cisco AnyConnect docs",
            }
        ],
    }
    monkeypatch.setattr(engine, "web_search_tool", lambda: mock_tool)

    state: AgentState = {
        "question": "How to connect to VPN?",
        "current_query": "VPN instructions",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": ["Prior KB chunk 1"],
    }

    result = engine.search_web(state)
    assert "retrieved_contexts" in result, "search_web must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == [
        "Title: Vendor Support\nURL: https://example.com/vpn\nContent: Official Cisco AnyConnect docs",
    ], "search_web must overwrite any prior/rejected KB contexts with only the web contexts"


@pytest.mark.asyncio
async def test_generate_from_kb_populates_retrieved_contexts_llm(monkeypatch):
    """generate_from_kb in engine includes exact chunk strings in retrieved_contexts."""
    mock_response = MagicMock(content="Follow these VPN steps.")
    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(engine, "llm", lambda: mock_llm_instance)

    docs = [Document(page_content="VPN instructions content.", metadata={"source": "kb/vpn.md"})]
    state: AgentState = {
        "question": "How to connect to VPN?",
        "current_query": "VPN instructions",
        "kb_docs": docs,
        "web_results": "",
        "kb_grade": "good",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": ["VPN instructions content."],
    }

    result = await engine.generate_from_kb(state)
    assert "retrieved_contexts" in result, "generate_from_kb must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == ["VPN instructions content."]


@pytest.mark.asyncio
async def test_generate_from_web_populates_retrieved_contexts_llm(monkeypatch):
    """generate_from_web in engine includes web snippets in retrieved_contexts."""
    mock_response = MagicMock(content="Vendor recommends reinstalling.")
    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(engine, "llm", lambda: mock_llm_instance)

    web_snippet = "Title: Vendor Support\nURL: https://example.com/vpn\nContent: Official Cisco AnyConnect docs"
    state: AgentState = {
        "question": "How to connect to VPN?",
        "current_query": "VPN instructions",
        "kb_docs": [],
        "web_results": web_snippet,
        "kb_grade": "weak",
        "web_grade": "good",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": [web_snippet],
    }

    result = await engine.generate_from_web(state)
    assert "retrieved_contexts" in result, "generate_from_web must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == [web_snippet]


@pytest.mark.asyncio
async def test_direct_answer_returns_empty_retrieved_contexts_llm(monkeypatch):
    """direct_answer in engine returns empty list for retrieved_contexts."""
    mock_response = MagicMock(content="Hello! How can I help you today?")
    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(engine, "llm", lambda: mock_llm_instance)

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

    result = await engine.direct_answer(state)
    assert "retrieved_contexts" in result, "direct_answer must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == []


def test_insufficient_returns_empty_retrieved_contexts_llm():
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


# ==============================================================================
# 3. fix_single_jev Node Tests
# ==============================================================================

def test_retrieve_kb_populates_retrieved_contexts_jev(monkeypatch):
    """retrieve_kb in engine populates retrieved_contexts with raw chunk strings."""
    docs = [
        Document(page_content="JEV Chunk 1: WiFi password.", metadata={"source": "kb/wifi.md"}),
        Document(page_content="JEV Chunk 2: Guest network.", metadata={"source": "kb/wifi.md"}),
    ]
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = docs
    monkeypatch.setattr(engine, "get_retriever", lambda: mock_retriever)

    state: AgentState = {
        "question": "What is the WiFi password?",
        "current_query": "WiFi password",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
    }

    result = engine.retrieve_kb(state)
    assert "retrieved_contexts" in result, "retrieve_kb must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == [
        "JEV Chunk 1: WiFi password.",
        "JEV Chunk 2: Guest network.",
    ]


def test_search_web_populates_retrieved_contexts_jev(monkeypatch):
    """search_web in engine populates web search snippets in retrieved_contexts without rejected KB chunks."""
    mock_tool = MagicMock()
    mock_tool.invoke.return_value = {
        "answer": "External WiFi docs",
        "results": [
            {
                "title": "Router Support",
                "url": "https://example.com/wifi",
                "content": "Reset router to factory defaults",
            }
        ],
    }
    monkeypatch.setattr(engine, "web_search_tool", lambda: mock_tool)

    state: AgentState = {
        "question": "How to fix WiFi?",
        "current_query": "WiFi fix",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": ["Prior JEV KB chunk"],
    }

    result = engine.search_web(state)
    assert "retrieved_contexts" in result, "search_web must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == [
        "Title: Router Support\nURL: https://example.com/wifi\nContent: Reset router to factory defaults",
    ], "search_web must overwrite any prior/rejected KB contexts with only the web contexts"


@pytest.mark.asyncio
async def test_generate_from_kb_populates_retrieved_contexts_jev(monkeypatch):
    """generate_from_kb in engine includes exact chunk strings in retrieved_contexts."""
    mock_response = MagicMock(content="WiFi password is securepass.")
    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(engine, "llm", lambda: mock_llm_instance)

    docs = [Document(page_content="WiFi password is securepass.", metadata={"source": "kb/wifi.md"})]
    state: AgentState = {
        "question": "What is the WiFi password?",
        "current_query": "WiFi password",
        "kb_docs": docs,
        "web_results": "",
        "kb_grade": "good",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": ["WiFi password is securepass."],
    }

    result = await engine.generate_from_kb(state)
    assert "retrieved_contexts" in result, "generate_from_kb must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == ["WiFi password is securepass."]


@pytest.mark.asyncio
async def test_generate_from_web_populates_retrieved_contexts_jev(monkeypatch):
    """generate_from_web in engine includes web snippets in retrieved_contexts."""
    mock_response = MagicMock(content="Router docs suggest power cycle.")
    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(engine, "llm", lambda: mock_llm_instance)

    web_snippet = "Title: Router Support\nURL: https://example.com/wifi\nContent: Power cycle router"
    state: AgentState = {
        "question": "How to fix WiFi?",
        "current_query": "WiFi fix",
        "kb_docs": [],
        "web_results": web_snippet,
        "kb_grade": "weak",
        "web_grade": "good",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "retrieved_contexts": [web_snippet],
    }

    result = await engine.generate_from_web(state)
    assert "retrieved_contexts" in result, "generate_from_web must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == [web_snippet]


@pytest.mark.asyncio
async def test_direct_answer_returns_empty_retrieved_contexts_jev(monkeypatch):
    """direct_answer in engine returns empty list for retrieved_contexts."""
    mock_response = MagicMock(content="Hello! How can I help you today?")
    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(engine, "llm", lambda: mock_llm_instance)

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

    result = await engine.direct_answer(state)
    assert "retrieved_contexts" in result, "direct_answer must return 'retrieved_contexts'"
    assert result["retrieved_contexts"] == []


def test_insufficient_returns_empty_retrieved_contexts_jev():
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


# ==============================================================================
# 4. ask() Return Contract Tests
# ==============================================================================

def test_ask_initial_state_contains_retrieved_contexts_llm(monkeypatch):
    """fix_single_llm.ask() initializes retrieved_contexts as [] in the initial state passed to invoke."""
    mock_invoke = MagicMock(return_value={
        "answer": "ok",
        "source_used": "direct",
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    })
    monkeypatch.setattr(fix_single_llm.agent_graph, "invoke", mock_invoke)

    res = fix_single_llm.ask("hello")
    mock_invoke.assert_called_once()
    initial_state = mock_invoke.call_args[0][0]
    assert "retrieved_contexts" in initial_state
    assert initial_state["retrieved_contexts"] == []
    assert "retrieved_contexts" in res


def test_ask_initial_state_contains_retrieved_contexts_jev(monkeypatch):
    """fix_single_jev.ask() initializes retrieved_contexts as [] in the initial state passed to invoke."""
    mock_invoke = MagicMock(return_value={
        "answer": "ok",
        "source_used": "direct",
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    })
    monkeypatch.setattr(fix_single_jev.agent_graph, "invoke", mock_invoke)

    res = fix_single_jev.ask("hello")
    mock_invoke.assert_called_once()
    initial_state = mock_invoke.call_args[0][0]
    assert "retrieved_contexts" in initial_state
    assert initial_state["retrieved_contexts"] == []
    assert "retrieved_contexts" in res


# ==============================================================================
# 5. Zero Coupling Test
# ==============================================================================

def test_strict_zero_coupling_between_llm_and_jev():
    """Verify zero-coupling between fix_single_llm.py and fix_single_jev.py is preserved."""
    repo_root = Path(__file__).resolve().parent.parent
    llm_path = repo_root / "app" / "rag" / "fix_single_llm.py"
    jev_path = repo_root / "app" / "rag" / "fix_single_jev.py"

    for path, forbidden in [(llm_path, "fix_single_jev"), (jev_path, "fix_single_llm")]:
        source = path.read_text(encoding="utf-8")
        parsed = ast.parse(source, filename=str(path))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert forbidden not in alias.name, f"{path} illegally imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert forbidden not in mod, f"{path} illegally imports from {mod}"
