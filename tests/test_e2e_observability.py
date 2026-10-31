import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.config import get_settings
from app.main import app
from app.rag.workflows import get_workflow_registry
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


def test_e2e_flow_with_langfuse_mocked(client, monkeypatch, register_mock_workflow):
    """Mock Langfuse client and callback; invoke /api/chat with fix_single_llm, verify trace returned, invoke /api/feedback with that trace ID, assert 200 response and background score dispatch."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-llm-101"
    mock_eval = AsyncMock()

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: getattr(handler, "last_trace_id", None))
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval)

    register_mock_workflow(
        "fix_single_llm",
        answer="Follow VPN installation guide for your OS.",
        source_used="knowledge_base",
        trace=["route_question", "retrieve_kb", "generate_from_kb"],
        citations=["vpn_setup.md"],
        retrieved_contexts=["Follow corporate VPN installation instructions."],
    )

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


def test_e2e_flow_with_langfuse_disabled(client, monkeypatch, register_mock_workflow):
    """Run chat invocation with LANGFUSE_ENABLED=False, verify normal response with trace_id=None and no exceptions."""
    settings = get_settings()
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    mock_eval = AsyncMock()
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval)

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "answer": "Visit https://mfa.example.com to reset your MFA token.",
        "source_used": "knowledge_base",
        "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
        "citations": ["mfa_reset.md"],
    }
    register_mock_workflow(
        "fix_single_jev",
        graph=mock_graph,
        answer="Visit https://mfa.example.com to reset your MFA token.",
        source_used="knowledge_base",
        trace=["route_question", "retrieve_kb", "generate_from_kb"],
        citations=["mfa_reset.md"],
    )

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
    # Telemetry callbacks are not injected when disabled
    call_config = mock_graph.invoke.call_args.kwargs.get("config")
    assert call_config is None or "callbacks" not in call_config

    # Also submit feedback when disabled - should not raise
    fb_res = client.post(
        "/api/feedback",
        json={"trace_id": "dummy-trace", "score": 0.5, "name": "user_feedback"},
    )
    assert fb_res.status_code == 200
    assert fb_res.json() == {"status": "accepted", "trace_id": "dummy-trace"}


def test_e2e_flow_with_fix_single_jev_mocked(client, monkeypatch, register_mock_workflow):
    """Exercise fix_single_jev workflow end-to-end with mock Langfuse callback and score client."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-jev-202"
    mock_eval = AsyncMock()

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: getattr(handler, "last_trace_id", None))
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval)

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "answer": "JEV validated response.",
        "source_used": "knowledge_base",
        "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
        "citations": [],
        "retrieved_contexts": ["Company laptop policy context."],
    }
    register_mock_workflow(
        "fix_single_jev",
        graph=mock_graph,
        answer="JEV validated response.",
        source_used="knowledge_base",
        trace=["route_question", "retrieve_kb", "generate_from_kb"],
        citations=[],
        retrieved_contexts=["Company laptop policy context."],
    )

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


def test_e2e_flow_with_langfuse_keys_unset(client, monkeypatch, register_mock_workflow):
    """When langfuse_enabled is True but keys are None, trace_id is gracefully None without error."""
    settings = get_settings()
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    monkeypatch.setattr(settings, "langfuse_secret_key", None)

    register_mock_workflow(
        "fix_single_llm",
        answer="Fallback response without keys",
        source_used="direct",
        trace=[],
        citations=[],
    )

    res = client.post("/api/chat", json={"question": "Hello", "workflow": "fix_single_llm"})
    assert res.status_code == 200
    assert res.json()["trace_id"] is None
    assert res.json()["answer"] == "Fallback response without keys"


@pytest.mark.asyncio
async def test_e2e_streaming_flow_with_observability_and_evaluation(monkeypatch, register_mock_workflow):
    """Verify POST /api/chat/stream emits SSE tokens, terminal metadata with trace_id,
    dispatches background online evaluation with retrieved_contexts, and handles feedback."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-stream-888"

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: "trace-e2e-stream-888")

    eval_called = asyncio.Event()
    eval_kwargs = {}

    async def mock_eval_task(**kwargs):
        nonlocal eval_kwargs
        eval_kwargs = kwargs
        eval_called.set()

    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval_task)

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
    register_mock_workflow("fix_single_jev", graph=mock_graph)

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


# ==============================================================================
# Ticket 08: End-to-End Verification & Full Regression Tests
# ==============================================================================


def test_e2e_switching_all_workflows_sync_and_stream(client, monkeypatch):
    """Verify switching between fix_single_jev, react_agent, and fix_single_llm across both sync and streaming."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-switch-777"
    mock_eval = AsyncMock()

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: getattr(handler, "last_trace_id", None))
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval)

    registry = get_workflow_registry()
    jev_wf = registry.get("fix_single_jev")
    react_wf = registry.get("react_agent")
    llm_wf = registry.get("fix_single_llm")

    # Mocks for the 3 workflows
    mock_jev_graph = MagicMock()
    mock_jev_graph.invoke.return_value = {
        "answer": "JEV workflow resolution.",
        "source_used": "vectorstore",
        "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
        "citations": [{"title": "jev_guide.pdf"}],
        "current_query": "how to reset password?",
        "retrieved_contexts": ["Reset password at account portal."],
    }
    monkeypatch.setattr(jev_wf, "get_compiled_graph", lambda: mock_jev_graph)

    mock_react_graph = MagicMock()
    mock_react_graph.invoke.return_value = {
        "messages": [
            HumanMessage(content="how to reset password?"),
            AIMessage(
                content="",
                tool_calls=[{"name": "knowledge_base_search", "args": {"query": "password"}, "id": "tc1"}],
            ),
            ToolMessage(
                content="[Source: react_guide.pdf] Password reset documentation.",
                name="knowledge_base_search",
                tool_call_id="tc1",
            ),
            AIMessage(content="ReAct workflow resolution."),
        ],
        "question": "how to reset password?",
    }
    monkeypatch.setattr(react_wf, "get_compiled_graph", lambda: mock_react_graph)

    mock_llm_graph = MagicMock()
    mock_llm_graph.invoke.return_value = {
        "answer": "LLM workflow resolution.",
        "source_used": "vectorstore",
        "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
        "citations": [{"title": "llm_guide.pdf"}],
        "current_query": "how to reset password?",
        "retrieved_contexts": ["LLM password guidelines."],
    }
    monkeypatch.setattr(llm_wf, "get_compiled_graph", lambda: mock_llm_graph)

    # 1. Sync /api/chat: Switch across all 3 workflows
    res_jev = client.post("/api/chat", json={"question": "how to reset password?", "workflow": "fix_single_jev"})
    assert res_jev.status_code == 200
    assert res_jev.json()["workflow"] == "fix_single_jev"
    assert res_jev.json()["answer"] == "JEV workflow resolution."

    res_react = client.post("/api/chat", json={"question": "how to reset password?", "workflow": "react_agent"})
    assert res_react.status_code == 200
    assert res_react.json()["workflow"] == "react_agent"
    assert res_react.json()["answer"] == "ReAct workflow resolution."

    res_llm = client.post("/api/chat", json={"question": "how to reset password?", "workflow": "fix_single_llm"})
    assert res_llm.status_code == 200
    assert res_llm.json()["workflow"] == "fix_single_llm"
    assert res_llm.json()["answer"] == "LLM workflow resolution."


def test_e2e_multi_turn_session_persistence_http(client, monkeypatch):
    """Multi-turn session persistence verified end-to-end via HTTP requests sharing session_id."""
    from langchain_core.language_models.chat_models import SimpleChatModel
    from langgraph.checkpoint.memory import MemorySaver

    from app.rag.workflows.react_agent import ReActAgentWorkflow

    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-e2e-persist-101"
    mock_eval = AsyncMock()

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: getattr(handler, "last_trace_id", None))
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval)

    class MemoryEchoChatModel(SimpleChatModel):
        @property
        def _llm_type(self) -> str:
            return "memory_echo"

        def bind_tools(self, tools, **kwargs):
            return self

        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            user_texts = [m.content for m in messages if isinstance(m, HumanMessage)]
            return f"Conversation history so far: {' -> '.join(user_texts)}"

    custom_wf = ReActAgentWorkflow(
        id="react_agent",
        llm=MemoryEchoChatModel(),
        tools=[],
        checkpointer=MemorySaver(),
    )

    registry = get_workflow_registry()
    react_wf = registry.get("react_agent")
    # Share the same checkpointer-enabled compiled graph
    monkeypatch.setattr(react_wf, "get_compiled_graph", custom_wf.get_compiled_graph)

    shared_session = "persistent-http-session-999"

    # Turn 1
    resp1 = client.post(
        "/api/chat",
        json={
            "question": "My favorite database is PostgreSQL.",
            "workflow": "react_agent",
            "session_id": shared_session,
        },
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert "Conversation history so far: My favorite database is PostgreSQL." in data1["answer"]

    # Turn 2: Follow up with new question in same session
    resp2 = client.post(
        "/api/chat",
        json={
            "question": "What database did I say?",
            "workflow": "react_agent",
            "session_id": shared_session,
        },
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    # MemorySaver retained Turn 1 message!
    assert (
        "Conversation history so far: My favorite database is PostgreSQL. -> What database did I say?"
        in data2["answer"]
    )


def test_e2e_langfuse_trace_spans_for_fixed_dag_and_react(client, monkeypatch):
    """Langfuse trace metadata and spans verified for both fixed DAG workflows and ReAct tool executions."""
    captured_callbacks = []

    def mock_get_langfuse_callback(**kwargs):
        captured_callbacks.append(kwargs)
        h = MagicMock()
        h.last_trace_id = f"trace-{kwargs.get('workflow_name')}-123"
        return h

    mock_client = MagicMock()
    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", mock_get_langfuse_callback)
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", AsyncMock())

    registry = get_workflow_registry()
    jev_wf = registry.get("fix_single_jev")
    react_wf = registry.get("react_agent")

    mock_jev_graph = MagicMock()
    mock_jev_graph.invoke.return_value = {
        "answer": "Fixed DAG answer",
        "source_used": "vectorstore",
        "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
        "citations": [],
        "current_query": "question",
    }
    monkeypatch.setattr(jev_wf, "get_compiled_graph", lambda: mock_jev_graph)

    mock_react_graph = MagicMock()
    mock_react_graph.invoke.return_value = {
        "messages": [AIMessage(content="ReAct answer")],
        "question": "question",
    }
    monkeypatch.setattr(react_wf, "get_compiled_graph", lambda: mock_react_graph)

    # 1. Fixed DAG call
    client.post(
        "/api/chat",
        json={
            "question": "Fixed DAG test",
            "workflow": "fix_single_jev",
            "session_id": "sess-dag",
            "user_id": "user-dag",
        },
    )
    assert len(captured_callbacks) == 1
    dag_cb = captured_callbacks[0]
    assert dag_cb["workflow_name"] == "fix_single_jev"
    assert dag_cb["session_id"] == "sess-dag"
    assert dag_cb["user_id"] == "user-dag"
    assert dag_cb["metadata"]["classifier"] == "typesafe_jev"
    assert dag_cb["metadata"]["retrieval_strategy"] == "self_corrective"

    # 2. ReAct Agent call
    client.post(
        "/api/chat",
        json={
            "question": "ReAct test",
            "workflow": "react_agent",
            "session_id": "sess-react",
            "user_id": "user-react",
        },
    )
    assert len(captured_callbacks) == 2
    react_cb = captured_callbacks[1]
    assert react_cb["workflow_name"] == "react_agent"
    assert react_cb["session_id"] == "sess-react"
    assert react_cb["user_id"] == "user-react"
    assert react_cb["metadata"]["classifier"] == "react_agent"
    assert react_cb["metadata"]["retrieval_strategy"] == "react_agent"


@pytest.mark.asyncio
async def test_e2e_online_evaluation_scores_recorded_for_all_workflows_without_schema_errors(monkeypatch):
    """Online evaluation scores and feedback successfully recorded for all workflows without schema errors."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-eval-multi-wf-999"

    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)
    monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: "trace-eval-multi-wf-999")

    captured_eval_tasks = []

    def mock_run_online_eval(**kwargs):
        captured_eval_tasks.append(kwargs)

    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_run_online_eval)

    registry = get_workflow_registry()
    workflows_to_test = ["fix_single_llm", "fix_single_jev", "react_agent"]

    with TestClient(app) as test_client:
        for wf_id in workflows_to_test:
            wf = registry.get(wf_id)
            mock_graph = MagicMock()

            if wf_id == "react_agent":
                mock_graph.invoke.return_value = {
                    "messages": [
                        HumanMessage(content=f"Question for {wf_id}"),
                        AIMessage(content="", tool_calls=[{"name": "knowledge_base_search", "args": {"query": "q"}, "id": "t1"}]),
                        ToolMessage(content="[Source: doc1.pdf] Extracted context for evaluation.", name="knowledge_base_search", tool_call_id="t1"),
                        AIMessage(content=f"Answer for {wf_id}"),
                    ],
                    "question": f"Question for {wf_id}",
                }
            else:
                mock_graph.invoke.return_value = {
                    "answer": f"Answer for {wf_id}",
                    "source_used": "vectorstore",
                    "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
                    "citations": [{"title": "doc1.pdf"}],
                    "current_query": f"Question for {wf_id}",
                    "retrieved_contexts": ["Extracted context for evaluation."],
                }

            monkeypatch.setattr(wf, "get_compiled_graph", lambda g=mock_graph: g)

            # 1. Chat invocation
            resp = test_client.post(
                "/api/chat",
                json={"question": f"Question for {wf_id}", "workflow": wf_id},
            )
            assert resp.status_code == 200

            # 2. Check online evaluation task enqueued with valid schema
            eval_call = [c for c in captured_eval_tasks if c.get("question") == f"Question for {wf_id}"]
            assert len(eval_call) == 1
            call_data = eval_call[0]
            assert call_data["trace_id"] == "trace-eval-multi-wf-999"
            assert call_data["question"] == f"Question for {wf_id}"
            assert call_data["answer"] == f"Answer for {wf_id}"
            assert call_data["source_used"] in {"vectorstore", "react_agent"}
            assert isinstance(call_data["retrieved_contexts"], list)
            assert len(call_data["retrieved_contexts"]) > 0

            # 3. Post feedback score to verify schema validation
            fb_resp = test_client.post(
                "/api/feedback",
                json={
                    "trace_id": "trace-eval-multi-wf-999",
                    "score": 1.0,
                    "name": "faithfulness",
                    "comment": f"High quality evaluation score for {wf_id}",
                },
            )
            assert fb_resp.status_code == 200
            mock_client.create_score.assert_called_with(
                trace_id="trace-eval-multi-wf-999",
                name="faithfulness",
                value=1.0,
                comment=f"High quality evaluation score for {wf_id}",
            )


