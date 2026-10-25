from unittest.mock import MagicMock
import pytest
from app.rag import fix_single_llm


def test_ask_llm_backward_compatibility(monkeypatch):
    """Calling ask() without keyword arguments passes expected initial state and empty/None config."""
    mock_invoke = MagicMock(return_value={"answer": "mock answer", "source_used": "direct"})
    monkeypatch.setattr(fix_single_llm.agent_graph, "invoke", mock_invoke)

    result = fix_single_llm.ask("hello")

    assert result == {"answer": "mock answer", "source_used": "direct"}
    mock_invoke.assert_called_once()
    args, kwargs = mock_invoke.call_args

    assert len(args) >= 1
    initial_state = args[0]
    assert initial_state["question"] == "hello"
    assert initial_state["current_query"] == ""
    assert initial_state["kb_docs"] == []
    assert initial_state["web_results"] == ""
    assert initial_state["kb_grade"] == ""
    assert initial_state["web_grade"] == ""
    assert initial_state["answer"] == ""
    assert initial_state["source_used"] == ""
    assert initial_state["retry_count"] == 0
    assert initial_state["trace"] == []
    assert initial_state["citations"] == []

    # Config should be None or empty
    config = kwargs.get("config") if "config" in kwargs else (args[1] if len(args) > 1 else None)
    assert config in (None, {})


def test_ask_llm_passes_callback(monkeypatch):
    """Calling ask() with a callback passes config={"callbacks": [callback]} to agent_graph.invoke."""
    mock_invoke = MagicMock(return_value={"answer": "mock answer"})
    monkeypatch.setattr(fix_single_llm.agent_graph, "invoke", mock_invoke)

    dummy_cb = object()
    result = fix_single_llm.ask("hello", callback=dummy_cb)

    assert result == {"answer": "mock answer"}
    mock_invoke.assert_called_once()
    args, kwargs = mock_invoke.call_args
    assert kwargs.get("config") == {"callbacks": [dummy_cb]}


def test_ask_llm_accepts_all_telemetry_kwargs(monkeypatch):
    """Calling ask() with session_id, user_id, metadata, tags, and callback is accepted."""
    mock_invoke = MagicMock(return_value={"answer": "mock answer"})
    monkeypatch.setattr(fix_single_llm.agent_graph, "invoke", mock_invoke)

    dummy_cb = object()
    result = fix_single_llm.ask(
        "hello",
        session_id="session-123",
        user_id="user-456",
        metadata={"env": "test"},
        tags=["unit-test"],
        callback=dummy_cb,
    )

    assert result == {"answer": "mock answer"}
    mock_invoke.assert_called_once()
    args, kwargs = mock_invoke.call_args
    assert kwargs.get("config") == {"callbacks": [dummy_cb]}
