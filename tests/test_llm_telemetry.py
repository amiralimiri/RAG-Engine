from unittest.mock import MagicMock
import pytest

from app.api.schemas import ChatRequest
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.runner import WorkflowRunner


@pytest.fixture
def runner_and_mock_graph():
    """Fixture providing a WorkflowRunner with fix_single_llm graph mocked."""
    registry = get_workflow_registry()
    wf_def = registry.get("fix_single_llm")
    mock_invoke = MagicMock(return_value={
        "answer": "mock answer",
        "source_used": "direct",
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    })
    mock_graph = MagicMock()
    mock_graph.invoke = mock_invoke
    wf_def._compiled_graph = mock_graph
    runner = WorkflowRunner(registry=registry)
    return runner, mock_invoke


def test_workflow_runner_telemetry_llm_execution(runner_and_mock_graph):
    """Executing fix_single_llm without keyword arguments passes expected initial state and clean config."""
    runner, mock_invoke = runner_and_mock_graph
    request = ChatRequest(question="hello", workflow="fix_single_llm")

    response, final_state = runner._execute_with_state(request)

    assert response.answer == "mock answer"
    assert response.source_used == "direct"
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
    assert initial_state["retrieved_contexts"] == []

    config = kwargs.get("config") if "config" in kwargs else (args[1] if len(args) > 1 else {})
    assert "callbacks" in config
    assert len(config["callbacks"]) == 1
    assert "tags" not in config or not config["tags"]


def test_ask_llm_passes_callback(runner_and_mock_graph):
    """Executing fix_single_llm with a callback passes config['callbacks'] = [callback]."""
    runner, mock_invoke = runner_and_mock_graph
    dummy_cb = object()
    request = ChatRequest(question="hello", workflow="fix_single_llm")

    response, _ = runner._execute_with_state(request, callback=dummy_cb)

    assert response.answer == "mock answer"
    mock_invoke.assert_called_once()
    _, kwargs = mock_invoke.call_args
    assert kwargs.get("config", {}).get("callbacks") == [dummy_cb]


def test_ask_llm_accepts_all_telemetry_kwargs(runner_and_mock_graph):
    """Executing fix_single_llm with session_id, user_id, metadata, tags, and callback is accepted."""
    runner, mock_invoke = runner_and_mock_graph
    dummy_cb = object()
    request = ChatRequest(
        question="hello",
        workflow="fix_single_llm",
        session_id="session-123",
        user_id="user-456",
    )

    response, _ = runner._execute_with_state(
        request,
        metadata={"env": "test"},
        tags=["unit-test"],
        callback=dummy_cb,
    )

    assert response.answer == "mock answer"
    mock_invoke.assert_called_once()
    _, kwargs = mock_invoke.call_args
    config = kwargs.get("config", {})
    assert config.get("callbacks") == [dummy_cb]
    assert config.get("tags") == ["unit-test"]
    assert config.get("metadata", {}).get("env") == "test"
    assert config.get("configurable", {}).get("thread_id") == "session-123"


def test_ask_llm_propagates_tags_and_metadata_without_callback(runner_and_mock_graph):
    """Executing fix_single_llm without callback still forwards tags and metadata in config."""
    runner, mock_invoke = runner_and_mock_graph
    request = ChatRequest(question="hello", workflow="fix_single_llm")

    response, _ = runner._execute_with_state(
        request,
        tags=["standalone-tag"],
        metadata={"source": "cli"},
    )

    assert response.answer == "mock answer"
    mock_invoke.assert_called_once()
    _, kwargs = mock_invoke.call_args
    config = kwargs.get("config", {})
    assert config.get("tags") == ["standalone-tag"]
    assert config.get("metadata", {}).get("source") == "cli"
