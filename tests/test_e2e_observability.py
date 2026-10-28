import asyncio
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS
from tests.test_chat_stream import parse_sse_lines


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_lifespan_startup_and_shutdown(monkeypatch):
    """Test FastAPI TestClient context manager; verify configure_logging is called on enter and flush_telemetry on exit."""
    mock_logging = MagicMock()
    mock_flush = MagicMock()
    monkeypatch.setattr("app.main.configure_logging", mock_logging)
    monkeypatch.setattr("app.main.flush_telemetry", mock_flush)

    mock_logging.assert_not_called()
    mock_flush.assert_not_called()

    with TestClient(app) as test_client:
        mock_logging.assert_called_once()
        mock_flush.assert_not_called()
        res = test_client.get("/api/health")
        assert res.status_code == 200

    mock_flush.assert_called_once()


def test_e2e_flow_with_langfuse_mocked(client, monkeypatch):
    """Mock Langfuse client and callback; invoke /api/chat with fix_single_llm, verify trace returned, invoke /api/feedback with that trace ID, assert 200 response and background score dispatch."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-llm-101"
    mock_eval = AsyncMock()

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.api.routes.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: getattr(handler, "last_trace_id", None))
    monkeypatch.setattr("app.api.routes.run_online_evaluation_task", mock_eval)

    def mock_llm_ask(question, **kwargs):
        return {
            "answer": "Follow VPN installation guide for your OS.",
            "source_used": "knowledge_base",
            "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
            "citations": ["vpn_setup.md"],
            "current_query": question,
            "retrieved_contexts": ["Follow corporate VPN installation instructions."],
        }

    monkeypatch.setitem(WORKFLOW_ASKS, "fix_single_llm", mock_llm_ask)

    # 1. Chat invocation
    chat_payload = {
        "question": "How do I install the corporate VPN?",
        "workflow": "fix_single_llm",
        "session_id": "session-llm-1",
        "user_id": "user-corp-1",
    }
    chat_res = client.post("/api/chat", json=chat_payload)
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert chat_data["workflow"] == "fix_single_llm"
    assert chat_data["answer"] == "Follow VPN installation guide for your OS."
    assert chat_data["source_used"] == "knowledge_base"
    assert chat_data["trace_id"] == "trace-e2e-llm-101"
    assert "vpn_setup.md" in chat_data["citations"]

    # Verify background online evaluation task enqueued
    mock_eval.assert_called_once_with(
        trace_id="trace-e2e-llm-101",
        question="How do I install the corporate VPN?",
        answer="Follow VPN installation guide for your OS.",
        source_used="knowledge_base",
        retrieved_contexts=["Follow corporate VPN installation instructions."],
    )

    # 2. Feedback invocation
    feedback_payload = {
        "trace_id": chat_data["trace_id"],
        "score": 1.0,
        "name": "user_feedback",
        "comment": "Perfect answer, thanks!",
    }
    fb_res = client.post("/api/feedback", json=feedback_payload)
    assert fb_res.status_code == 200
    assert fb_res.json() == {"status": "accepted", "trace_id": "trace-e2e-llm-101"}

    # Assert background score dispatch to Langfuse client
    mock_client.create_score.assert_called_once_with(
        trace_id="trace-e2e-llm-101",
        name="user_feedback",
        value=1.0,
        comment="Perfect answer, thanks!",
    )


def test_e2e_flow_with_langfuse_disabled(client, monkeypatch):
    """Run chat invocation with LANGFUSE_ENABLED=False, verify normal response with trace_id=None and no exceptions."""
    settings = get_settings()
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    mock_eval = AsyncMock()
    monkeypatch.setattr("app.api.routes.run_online_evaluation_task", mock_eval)

    def mock_jev_ask(question, **kwargs):
        # Assert callback is None when telemetry is disabled
        assert kwargs.get("callback") is None
        return {
            "answer": "Visit https://mfa.example.com to reset your MFA token.",
            "source_used": "knowledge_base",
            "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
            "citations": ["mfa_reset.md"],
            "current_query": question,
        }

    monkeypatch.setitem(WORKFLOW_ASKS, "fix_single_jev", mock_jev_ask)

    payload = {
        "question": "How do I reset my MFA?",
        "workflow": "fix_single_jev",
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Visit https://mfa.example.com to reset your MFA token."
    assert data["trace_id"] is None
    assert data["workflow"] == "fix_single_jev"
    # When trace_id is None, online evaluation is skipped
    mock_eval.assert_not_called()

    # Also submit feedback when disabled - should not raise
    fb_res = client.post(
        "/api/feedback",
        json={"trace_id": "dummy-trace", "score": 0.5, "name": "user_feedback"},
    )
    assert fb_res.status_code == 200
    assert fb_res.json() == {"status": "accepted", "trace_id": "dummy-trace"}


def test_e2e_flow_with_fix_single_jev_mocked(client, monkeypatch):
    """Exercise fix_single_jev workflow end-to-end with mock Langfuse callback and score client."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-jev-202"
    mock_eval = AsyncMock()

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.api.routes.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: getattr(handler, "last_trace_id", None))
    monkeypatch.setattr("app.api.routes.run_online_evaluation_task", mock_eval)

    def mock_jev_ask(question, **kwargs):
        assert kwargs.get("callback") is mock_handler
        return {
            "answer": "JEV validated response.",
            "source_used": "knowledge_base",
            "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
            "citations": [],
            "current_query": question,
            "retrieved_contexts": ["Company laptop policy context."],
        }

    monkeypatch.setitem(WORKFLOW_ASKS, "fix_single_jev", mock_jev_ask)

    chat_payload = {
        "question": "What is the policy on company laptops?",
        "workflow": "fix_single_jev",
        "session_id": "session-jev-2",
        "user_id": "user-corp-2",
    }
    chat_res = client.post("/api/chat", json=chat_payload)
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert chat_data["workflow"] == "fix_single_jev"
    assert chat_data["trace_id"] == "trace-e2e-jev-202"

    # Verify background online evaluation task enqueued
    mock_eval.assert_called_once_with(
        trace_id="trace-e2e-jev-202",
        question="What is the policy on company laptops?",
        answer="JEV validated response.",
        source_used="knowledge_base",
        retrieved_contexts=["Company laptop policy context."],
    )

    fb_res = client.post(
        "/api/feedback",
        json={
            "trace_id": "trace-e2e-jev-202",
            "score": 0.0,
            "name": "thumbs_down",
            "comment": "Did not solve my problem",
        },
    )
    assert fb_res.status_code == 200
    mock_client.create_score.assert_called_once_with(
        trace_id="trace-e2e-jev-202",
        name="thumbs_down",
        value=0.0,
        comment="Did not solve my problem",
    )


def test_e2e_flow_with_langfuse_keys_unset(client, monkeypatch):
    """When langfuse_enabled is True but keys are None, trace_id is gracefully None without error."""
    settings = get_settings()
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    monkeypatch.setattr(settings, "langfuse_secret_key", None)

    def mock_ask(question, **kwargs):
        assert kwargs.get("callback") is None
        return {
            "answer": "Fallback response without keys",
            "source_used": "direct",
            "trace": [],
            "citations": [],
            "current_query": question,
        }

    monkeypatch.setitem(WORKFLOW_ASKS, "fix_single_llm", mock_ask)

    res = client.post("/api/chat", json={"question": "Hello", "workflow": "fix_single_llm"})
    assert res.status_code == 200
    assert res.json()["trace_id"] is None
    assert res.json()["answer"] == "Fallback response without keys"


@pytest.mark.asyncio
async def test_e2e_streaming_flow_with_observability_and_evaluation(monkeypatch):
    """Verify POST /api/chat/stream emits SSE tokens, terminal metadata with trace_id,
    dispatches background online evaluation with retrieved_contexts, and handles feedback."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-stream-888"

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.api.routes.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-e2e-stream-888")

    eval_called = asyncio.Event()
    eval_kwargs = {}

    async def mock_eval_task(**kwargs):
        nonlocal eval_kwargs
        eval_kwargs = kwargs
        eval_called.set()

    monkeypatch.setattr("app.api.routes.run_online_evaluation_task", mock_eval_task)

    async def mock_astream_events(*args, **kwargs):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "retrieve_kb"}, "data": {}}
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "retrieve_kb"},
            "data": {
                "output": {
                    "retrieved_contexts": ["KB doc content on MFA tokens."],
                    "trace": ["retrieve_kb"],
                }
            },
        }
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate_from_kb"},
            "data": {"chunk": {"content": "MFA token can be reset "}},
        }
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate_from_kb"},
            "data": {"chunk": {"content": "via self-service."}},
        }
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate_from_kb"},
            "data": {
                "output": {
                    "answer": "MFA token can be reset via self-service.",
                    "source_used": "knowledge_base",
                    "citations": ["mfa.md"],
                    "retrieved_contexts": ["KB doc content on MFA tokens."],
                }
            },
        }

    mock_graph = MagicMock()
    mock_graph.astream_events = mock_astream_events
    monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_jev", mock_graph)

    # 1. Stream chat request
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            "/api/chat/stream",
            json={
                "question": "How do I reset my MFA token?",
                "workflow": "fix_single_jev",
            },
        ) as resp:
            assert resp.status_code == 200
            lines = [line async for line in resp.aiter_lines()]

    # 2. Verify SSE events
    events = parse_sse_lines(lines)
    meta_events = [e for e in events if e["event"] == "metadata"]
    assert len(meta_events) == 1
    assert meta_events[0]["data"]["trace_id"] == "trace-e2e-stream-888"
    assert meta_events[0]["data"]["source_used"] == "knowledge_base"

    # 3. Verify online evaluation was triggered
    await asyncio.wait_for(eval_called.wait(), timeout=2.0)
    assert eval_kwargs["trace_id"] == "trace-e2e-stream-888"
    assert eval_kwargs["question"] == "How do I reset my MFA token?"
    assert eval_kwargs["answer"] == "MFA token can be reset via self-service."
    assert eval_kwargs["source_used"] == "knowledge_base"
    assert eval_kwargs["retrieved_contexts"] == ["KB doc content on MFA tokens."]

    # 4. Feedback endpoint accepts the trace_id
    with TestClient(app) as test_client:
        fb_res = test_client.post(
            "/api/feedback",
            json={
                "trace_id": "trace-e2e-stream-888",
                "score": 1.0,
                "name": "user_feedback",
                "comment": "Super helpful!",
            },
        )
        assert fb_res.status_code == 200
        mock_client.create_score.assert_called_once_with(
            trace_id="trace-e2e-stream-888",
            name="user_feedback",
            value=1.0,
            comment="Super helpful!",
        )


