from unittest.mock import MagicMock
import pytest

from app.api.schemas import ChatRequest
from app.rag.classifiers import JevClassifierAdapter, _estimate_jev_tokens
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.runner import WorkflowRunner


def route_question_jev(state, config=None):
    adapter = JevClassifierAdapter()
    run_cfg = dict(config) if config else {}
    if "trace_id" in state and "metadata" not in run_cfg:
        run_cfg["metadata"] = {"trace_id": state["trace_id"]}
    decision = adapter.route(state["question"], config=run_cfg)
    return {
        "current_query": state["question"],
        "source_used": decision.route,
        "trace": [f"Router → {decision.route.upper()}"],
    }


def grade_kb_jev(state, config=None):
    adapter = JevClassifierAdapter()
    run_cfg = dict(config) if config else {}
    if "trace_id" in state and "metadata" not in run_cfg:
        run_cfg["metadata"] = {"trace_id": state["trace_id"]}
    grade = adapter.grade_kb(
        question=state["question"],
        current_query=state.get("current_query", ""),
        kb_docs=state.get("kb_docs", []),
        config=run_cfg,
    )
    return {
        "kb_grade": grade.grade,
        "trace": [f"KB evidence grade → {grade.grade.upper()}"],
    }


def test_estimate_jev_tokens():
    """_estimate_jev_tokens correctly estimates tokens based on character length (max(1, len // 4))."""
    # Empty string should yield minimum 1 token
    res_empty = _estimate_jev_tokens("", "")
    assert res_empty == {"input": 1, "output": 1, "total": 2}

    # Short strings (< 4 chars) should yield minimum 1 token
    res_short = _estimate_jev_tokens("hi", "no")
    assert res_short == {"input": 1, "output": 1, "total": 2}

    # Strings with exact multiples of 4
    res_exact = _estimate_jev_tokens("12345678", "abcd")
    assert res_exact == {"input": 2, "output": 1, "total": 3}

    # Longer string
    prompt = "This is a prompt for testing token estimation."  # 46 chars -> 46 // 4 = 11
    output = "kb"  # 2 chars -> max(1, 2 // 4) = 1
    res_longer = _estimate_jev_tokens(prompt, output)
    assert res_longer == {
        "input": len(prompt) // 4,
        "output": 1,
        "total": (len(prompt) // 4) + 1,
    }
    assert res_longer["input"] == 11
    assert res_longer["output"] == 1
    assert res_longer["total"] == 12


@pytest.fixture
def jev_runner_and_mock_graph():
    """Fixture providing a WorkflowRunner with fix_single_jev graph mocked."""
    registry = get_workflow_registry()
    wf_def = registry.get("fix_single_jev")
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


def test_workflow_runner_telemetry_jev_execution(jev_runner_and_mock_graph):
    """Executing fix_single_jev without keyword arguments passes expected initial state and clean config."""
    runner, mock_invoke = jev_runner_and_mock_graph
    request = ChatRequest(question="hello", workflow="fix_single_jev")

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


def test_ask_jev_passes_callback(jev_runner_and_mock_graph):
    """Executing fix_single_jev with a callback passes config['callbacks'] = [callback]."""
    runner, mock_invoke = jev_runner_and_mock_graph
    dummy_cb = object()
    request = ChatRequest(question="hello", workflow="fix_single_jev")

    response, _ = runner._execute_with_state(request, callback=dummy_cb)

    assert response.answer == "mock answer"
    mock_invoke.assert_called_once()
    _, kwargs = mock_invoke.call_args
    assert kwargs.get("config", {}).get("callbacks") == [dummy_cb]


def test_ask_jev_accepts_all_telemetry_kwargs(jev_runner_and_mock_graph):
    """Executing fix_single_jev with session_id, user_id, metadata, tags, and callback is accepted."""
    runner, mock_invoke = jev_runner_and_mock_graph
    dummy_cb = object()
    request = ChatRequest(
        question="hello",
        workflow="fix_single_jev",
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


def test_ask_jev_propagates_tags_and_metadata_without_callback(jev_runner_and_mock_graph):
    """Executing fix_single_jev without callback still forwards tags and metadata in config."""
    runner, mock_invoke = jev_runner_and_mock_graph
    request = ChatRequest(question="hello", workflow="fix_single_jev")

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


def test_jev_node_fails_open_when_langfuse_client_fails(monkeypatch):
    """Mock get_langfuse_client returning a mock whose generation recording throws; verify route_question_jev succeeds."""
    mock_jev = MagicMock()
    mock_jev_result = MagicMock()
    mock_jev_result.choices = {"route": MagicMock(choice="kb")}
    mock_jev.invoke.return_value = mock_jev_result
    monkeypatch.setattr("app.rag.classifiers.TypeSafeClassifier", lambda **kwargs: mock_jev)

    mock_client = MagicMock()
    mock_client.start_observation.side_effect = RuntimeError("Langfuse API unavailable")
    mock_client.generation.side_effect = RuntimeError("Langfuse API unavailable")
    monkeypatch.setattr("app.rag.classifiers.get_langfuse_client", lambda: mock_client)

    state = {
        "question": "How do I reset my password?",
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
    }

    result = route_question_jev(state)
    assert result["source_used"] == "kb"
    assert result["current_query"] == "How do I reset my password?"
    assert "Router → KB" in result["trace"]


def test_jev_node_when_langfuse_client_none(monkeypatch):
    """When get_langfuse_client() returns None, route_question_jev executes cleanly."""
    mock_jev = MagicMock()
    mock_jev_result = MagicMock()
    mock_jev_result.choices = {"route": MagicMock(choice="direct")}
    mock_jev.invoke.return_value = mock_jev_result
    monkeypatch.setattr("app.rag.classifiers.TypeSafeClassifier", lambda **kwargs: mock_jev)

    monkeypatch.setattr("app.rag.classifiers.get_langfuse_client", lambda: None)

    state = {
        "question": "Hello",
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
    }

    result = route_question_jev(state)
    assert result["source_used"] == "direct"
    assert "Router → DIRECT" in result["trace"]


def test_jev_nodes_emit_generation_metadata(monkeypatch):
    """When langfuse client is active, route_question_jev and grade_kb_jev record generation span."""
    mock_jev = MagicMock()
    mock_route_res = MagicMock()
    mock_route_res.choices = {"route": MagicMock(choice="kb")}
    mock_grade_res = MagicMock()
    mock_grade_res.choices = {"grade": MagicMock(choice="good")}

    def mock_jev_invoke(payload):
        if "route" in payload.get("questions", {}):
            return mock_route_res
        return mock_grade_res

    mock_jev.invoke.side_effect = mock_jev_invoke
    monkeypatch.setattr("app.rag.classifiers.TypeSafeClassifier", lambda **kwargs: mock_jev)

    mock_client = MagicMock()
    mock_observation = MagicMock()
    mock_client.start_observation.return_value = mock_observation
    monkeypatch.setattr("app.rag.classifiers.get_langfuse_client", lambda: mock_client)

    # Test route_question_jev
    state = {
        "question": "How to reset VPN?",
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
    }
    route_res = route_question_jev(state)
    assert route_res["source_used"] == "kb"
    assert mock_client.start_observation.called
    obs_call_args = mock_client.start_observation.call_args[1]
    assert obs_call_args["name"] == "jev_router"
    assert obs_call_args["as_type"] == "generation"
    assert obs_call_args["input"] == "How to reset VPN?"
    assert obs_call_args["output"] == "kb"
    assert obs_call_args["usage_details"]["total"] >= 2
    assert "cost_details" in obs_call_args
    assert obs_call_args["cost_details"]["total"] > 0
    mock_observation.end.assert_called_once()

    # Test grade_kb_jev
    mock_observation.reset_mock()
    doc_mock = MagicMock()
    doc_mock.metadata = {"source": "vpn_guide.md"}
    doc_mock.page_content = "To reset VPN, go to settings."
    state["kb_docs"] = [doc_mock]

    grade_res = grade_kb_jev(state)
    assert grade_res["kb_grade"] == "good"
    obs_call_args2 = mock_client.start_observation.call_args[1]
    assert obs_call_args2["name"] == "jev_kb_grader"
    assert obs_call_args2["as_type"] == "generation"
    assert obs_call_args2["output"] == "good"
    assert "cost_details" in obs_call_args2
    assert obs_call_args2["cost_details"]["total"] > 0
    mock_observation.end.assert_called_once()


def test_jev_nodes_emit_generation_with_trace_context(monkeypatch):
    """When state contains trace_id, route_question_jev passes trace_context to start_observation."""
    mock_jev = MagicMock()
    mock_route_res = MagicMock()
    mock_route_res.choices = {"route": MagicMock(choice="kb")}
    mock_jev.invoke.return_value = mock_route_res
    monkeypatch.setattr("app.rag.classifiers.TypeSafeClassifier", lambda **kwargs: mock_jev)

    mock_client = MagicMock()
    mock_observation = MagicMock()
    mock_client.start_observation.return_value = mock_observation
    monkeypatch.setattr("app.rag.classifiers.get_langfuse_client", lambda: mock_client)

    state = {
        "question": "How to reset VPN?",
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
        "trace_id": "trace-root-999",
    }
    route_res = route_question_jev(state)
    assert route_res["source_used"] == "kb"
    assert mock_client.start_observation.called
    obs_call_args = mock_client.start_observation.call_args[1]
    assert obs_call_args["trace_context"] == {"trace_id": "trace-root-999"}


def test_jev_nodes_emit_generation_with_trace_context_from_contextvars(monkeypatch):
    """When state has no trace_id, _record_jev_generation pulls active trace_id from structlog contextvars."""
    import structlog
    structlog.contextvars.bind_contextvars(trace_id="ctx-trace-abc-123")

    mock_jev = MagicMock()
    mock_jev.invoke.return_value.choices = {"route": MagicMock(choice="kb")}
    monkeypatch.setattr("app.rag.classifiers.TypeSafeClassifier", lambda **kwargs: mock_jev)

    mock_client = MagicMock()
    mock_obs = MagicMock()
    mock_client.start_observation.return_value = mock_obs
    monkeypatch.setattr("app.rag.classifiers.get_langfuse_client", lambda: mock_client)

    state = {
        "question": "How to reset VPN?",
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
    }
    route_question_jev(state)
    obs_args = mock_client.start_observation.call_args[1]
    assert obs_args["trace_context"] == {"trace_id": "ctx-trace-abc-123"}


def test_jev_nodes_emit_generation_with_trace_context_from_config(monkeypatch):
    """When state has no trace_id, _record_jev_generation extracts trace_id from config callbacks or metadata."""
    import structlog
    structlog.contextvars.unbind_contextvars("trace_id")

    mock_jev = MagicMock()
    mock_jev.invoke.return_value.choices = {"route": MagicMock(choice="kb")}
    monkeypatch.setattr("app.rag.classifiers.TypeSafeClassifier", lambda **kwargs: mock_jev)

    mock_client = MagicMock()
    mock_obs = MagicMock()
    mock_client.start_observation.return_value = mock_obs
    monkeypatch.setattr("app.rag.classifiers.get_langfuse_client", lambda: mock_client)

    state = {
        "question": "How to reset VPN?",
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
    }

    # 1. via config metadata
    route_question_jev(state, config={"metadata": {"trace_id": "meta-trace-456"}})
    obs_args = mock_client.start_observation.call_args[1]
    assert obs_args["trace_context"] == {"trace_id": "meta-trace-456"}

    # 2. via config callback handler
    mock_client.start_observation.reset_mock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "handler-trace-789"
    route_question_jev(state, config={"callbacks": [mock_handler]})
    obs_args2 = mock_client.start_observation.call_args[1]
    assert obs_args2["trace_context"] == {"trace_id": "handler-trace-789"}
