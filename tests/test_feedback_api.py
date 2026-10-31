from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes import _submit_feedback_task
from app.main import app
from tests.conftest import MockWorkflowDefinition


@pytest.fixture(autouse=True)
def mock_online_evaluation(monkeypatch):
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", AsyncMock())
    monkeypatch.setattr("app.services.evaluation.run_online_evaluation_task", AsyncMock())


@pytest.fixture
def client():
    return TestClient(app)


def test_chat_endpoint_returns_trace_id(client, monkeypatch, register_mock_workflow):
    """Call POST /api/chat with mock ask function, verify 'trace_id' is present in response dict."""
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-test-123"

    monkeypatch.setattr(
        "app.rag.workflows.runner.get_langfuse_callback",
        lambda **kwargs: mock_handler,
    )
    monkeypatch.setattr(
        "app.rag.workflows.runner.extract_trace_id",
        lambda handler: getattr(handler, "last_trace_id", None),
    )

    register_mock_workflow(
        "fix_single_jev",
        answer="Mock IT response",
        source_used="knowledge_base",
        trace=["retrieval"],
        citations=[],
    )

    payload = {
        "question": "How do I connect to VPN?",
        "workflow": "fix_single_jev",
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "trace_id" in data
    assert data["trace_id"] == "trace-test-123"
    assert data["answer"] == "Mock IT response"


def test_chat_endpoint_passes_session_and_user_id_to_ask(client, monkeypatch, clean_registry):
    """POST /api/chat passes session_id, user_id, and callback to the respective workflow."""
    captured_request = None
    captured_config = None
    mock_handler = MagicMock()

    monkeypatch.setattr(
        "app.rag.workflows.runner.get_langfuse_callback",
        lambda **kwargs: mock_handler,
    )

    mock_graph = MagicMock()

    def _invoke(state, config=None):
        nonlocal captured_config
        captured_config = config
        return {
            "answer": "Answer with session",
            "source_used": "direct",
            "trace": [],
            "citations": [],
            "current_query": state.get("question"),
        }

    mock_graph.invoke = _invoke

    class CapturedMockWorkflow(MockWorkflowDefinition):
        def to_initial_state(self, request, trace_id=None):
            nonlocal captured_request
            captured_request = request
            return super().to_initial_state(request, trace_id=trace_id)

    clean_registry.register(
        CapturedMockWorkflow(
            "fix_single_llm",
            graph=mock_graph,
            answer="Answer with session",
            source_used="direct",
            trace=[],
        ),
        override=True,
    )

    payload = {
        "question": "Need help with printer",
        "workflow": "fix_single_llm",
        "session_id": "sess-test-999",
        "user_id": "user-test-888",
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    assert captured_request is not None
    assert captured_request.session_id == "sess-test-999"
    assert captured_request.user_id == "user-test-888"
    assert captured_config is not None
    assert captured_config.get("callbacks") == [mock_handler]


def test_feedback_endpoint_accepts_valid_payload(client, monkeypatch):
    """Post valid feedback ({"trace_id": "tr-123", "score": 1.0, "comment": "great"}), assert HTTP 200 and accepted status."""
    submitted_tasks = []

    def mock_submit_task(*args, **kwargs):
        submitted_tasks.append((args, kwargs))

    monkeypatch.setattr("app.api.routes._submit_feedback_task", mock_submit_task)

    payload = {
        "trace_id": "tr-123",
        "score": 1.0,
        "name": "user_feedback",
        "comment": "great",
    }
    response = client.post("/api/feedback", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "accepted", "trace_id": "tr-123"}
    # BackgroundTasks runs during TestClient request cycle
    assert len(submitted_tasks) == 1
    assert submitted_tasks[0][1]["trace_id"] == "tr-123"
    assert submitted_tasks[0][1]["score"] == 1.0
    assert submitted_tasks[0][1]["name"] == "user_feedback"
    assert submitted_tasks[0][1]["comment"] == "great"


def test_feedback_endpoint_validates_score_bounds(client):
    """Post score 1.5 and -0.2, assert HTTP 422 for both."""
    res_high = client.post("/api/feedback", json={"trace_id": "tr-123", "score": 1.5})
    assert res_high.status_code == 422

    res_low = client.post("/api/feedback", json={"trace_id": "tr-123", "score": -0.2})
    assert res_low.status_code == 422

    # Also test valid boundaries 0.0 and 1.0
    res_zero = client.post("/api/feedback", json={"trace_id": "tr-123", "score": 0.0})
    assert res_zero.status_code == 200

    res_one = client.post("/api/feedback", json={"trace_id": "tr-123", "score": 1.0})
    assert res_one.status_code == 200


def test_feedback_task_executes_langfuse_score(monkeypatch):
    """Invoke _submit_feedback_task directly with mock client, verify client.create_score called with exact arguments."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()

    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    _submit_feedback_task(
        trace_id="tr-456",
        score=0.75,
        name="user_feedback",
        comment="helpful explanation",
    )

    mock_client.create_score.assert_called_once_with(
        trace_id="tr-456",
        name="user_feedback",
        value=0.75,
        comment="helpful explanation",
    )


def test_feedback_task_falls_back_to_score_method(monkeypatch):
    """Invoke _submit_feedback_task when client only defines score()."""
    mock_client = MagicMock(spec=["score"])
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    _submit_feedback_task(
        trace_id="tr-fallback",
        score=0.5,
        name="user_feedback",
        comment=None,
    )

    mock_client.score.assert_called_once_with(
        trace_id="tr-fallback",
        name="user_feedback",
        value=0.5,
        comment=None,
    )


def test_feedback_task_handles_client_exception_gracefully(monkeypatch):
    """If Langfuse client fails in the background task, the error is logged without crashing."""
    mock_client = MagicMock()
    mock_client.create_score.side_effect = RuntimeError("Langfuse API unavailable")

    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    # Should not raise exception
    _submit_feedback_task(
        trace_id="tr-err",
        score=1.0,
        name="user_feedback",
        comment="error test",
    )
