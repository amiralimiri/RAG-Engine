"""Tests for modernized multi-workflow transport layer and SSE token filtering in app/api/routes.py."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCallChunk

from app.api.schemas import (
    ChatResponse,
    WorkflowListResponse,
)
from app.main import app
from app.rag.workflows import (
    get_workflow_registry,
)


def parse_sse_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Parse raw SSE lines into a list of {'event': str, 'data': dict} objects."""
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    current_data: list[str] = []

    for line in lines:
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current_data.append(line[len("data:"):].strip())
        elif line == "" and current_event is not None:
            raw = "\n".join(current_data)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            events.append({"event": current_event, "data": parsed})
            current_event = None
            current_data = []

    if current_event is not None and current_data:
        raw = "\n".join(current_data)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        events.append({"event": current_event, "data": parsed})

    return events


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ==============================================================================
# 1. GET /api/workflows Introspection Tests
# ==============================================================================


class TestGetWorkflowsEndpoint:
    """Validate GET /api/workflows endpoint capabilities."""

    def test_get_workflows_schema_and_metadata(self, client: TestClient):
        """GET /api/workflows returns default, workflow list, and rich capability details."""
        resp = client.get("/api/workflows")
        assert resp.status_code == 200

        data = resp.json()
        validated = WorkflowListResponse(**data)

        assert validated.default == "fix_single_jev"
        assert "fix_single_jev" in validated.workflows
        assert "fix_single_llm" in validated.workflows
        assert "react_agent" in validated.workflows

        # Check detail richness
        details_by_id = {d.id: d for d in validated.details}
        assert "fix_single_jev" in details_by_id
        assert "fix_single_llm" in details_by_id
        assert "react_agent" in details_by_id

        # Verify ReAct agent metadata
        react_info = details_by_id["react_agent"]
        assert react_info.is_conversational is True
        assert react_info.supports_streaming is True
        assert "agent" in react_info.generation_nodes
        assert "agent" in react_info.step_labels
        assert "tools" in react_info.step_labels

        # Verify fixed DAG metadata
        jev_info = details_by_id["fix_single_jev"]
        assert jev_info.is_conversational is False
        assert "generate_from_kb" in jev_info.generation_nodes
        assert "retrieve_kb" in jev_info.step_labels


# ==============================================================================
# 2. POST /api/chat Multi-Workflow Execution Tests
# ==============================================================================


class TestPostChatMultiWorkflow:
    """Validate POST /api/chat execution routed through WorkflowRegistry."""

    def test_post_chat_unknown_workflow_returns_400(self, client: TestClient):
        """Unknown workflow IDs return HTTP 400 Bad Request."""
        resp = client.post(
            "/api/chat",
            json={"question": "What is the policy?", "workflow": "nonexistent_workflow"},
        )
        assert resp.status_code == 400
        assert "Unknown workflow: nonexistent_workflow" in resp.json()["detail"]

    def test_post_chat_executes_registered_workflow(self, client: TestClient, monkeypatch):
        """POST /api/chat calls registered workflow and constructs valid ChatResponse."""
        registry = get_workflow_registry()
        workflow = registry.get("fix_single_jev")

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "answer": "Connect via VPN gateway 10.0.0.1.",
            "source_used": "vectorstore",
            "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
            "citations": [{"title": "vpn.pdf", "url": "", "type": "private_kb"}],
            "current_query": "how to vpn?",
            "retrieved_contexts": ["VPN doc excerpt"],
            "trace_id": "test-trace-chat-01",
        }

        monkeypatch.setattr(workflow, "get_compiled_graph", lambda: mock_graph)

        captured_eval = {}

        def mock_eval_task(**kwargs):
            captured_eval.update(kwargs)

        monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval_task)

        resp = client.post(
            "/api/chat",
            json={
                "question": "how to vpn?",
                "workflow": "fix_single_jev",
                "session_id": "sess-test-01",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        chat_resp = ChatResponse(**data)
        assert chat_resp.workflow == "fix_single_jev"
        assert chat_resp.answer == "Connect via VPN gateway 10.0.0.1."
        assert chat_resp.source_used == "vectorstore"
        assert len(chat_resp.citations) == 1
        assert chat_resp.citations[0]["title"] == "vpn.pdf"

        # Verify thread_id was passed in configurable
        assert mock_graph.invoke.called
        call_config = mock_graph.invoke.call_args[1].get("config") or mock_graph.invoke.call_args[0][1]
        assert call_config.get("configurable", {}).get("thread_id") == "sess-test-01"

    def test_post_chat_react_agent_execution_with_messages(self, client: TestClient, monkeypatch):
        """POST /api/chat handles ReAct agent conversational messages and returns citations."""
        registry = get_workflow_registry()
        workflow = registry.get("react_agent")

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "messages": [
                HumanMessage(content="Hello"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "knowledge_base_search", "args": {"query": "VPN"}, "id": "tc1"}],
                ),
                ToolMessage(
                    content="[Source: vpn_guide.pdf] Use Okta MFA to log into VPN.",
                    name="knowledge_base_search",
                    tool_call_id="tc1",
                ),
                AIMessage(content="You should use Okta MFA to log into the corporate VPN."),
            ],
            "question": "How do I log into VPN?",
        }

        monkeypatch.setattr(workflow, "get_compiled_graph", lambda: mock_graph)
        monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", MagicMock())

        resp = client.post(
            "/api/chat",
            json={
                "question": "How do I log into VPN?",
                "workflow": "react_agent",
                "session_id": "sess-react-02",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        chat_resp = ChatResponse(**data)
        assert chat_resp.workflow == "react_agent"
        assert "Okta MFA" in chat_resp.answer
        assert chat_resp.source_used == "vectorstore"
        assert len(chat_resp.citations) == 1
        assert chat_resp.citations[0]["title"] == "vpn_guide.pdf"

    def test_post_chat_error_fallback_handling(self, client: TestClient, monkeypatch):
        """When workflow execution throws, workflow.handle_error is invoked for graceful fallback."""
        registry = get_workflow_registry()
        workflow = registry.get("fix_single_jev")

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("Pinecone cluster unreachable")

        monkeypatch.setattr(workflow, "get_compiled_graph", lambda: mock_graph)

        resp = client.post(
            "/api/chat",
            json={"question": "What is my IP?", "workflow": "fix_single_jev"},
        )
        assert resp.status_code == 200
        data = resp.json()

        chat_resp = ChatResponse(**data)
        assert chat_resp.source_used == "error_fallback"
        assert "unexpected issue" in chat_resp.answer.lower()
        assert any("error fallback" in t.lower() for t in chat_resp.trace)


# ==============================================================================
# 3. POST /api/chat/stream SSE & Token Filtering Tests
# ==============================================================================


@pytest.mark.asyncio
class TestPostChatStreamMultiWorkflow:
    """Validate POST /api/chat/stream dynamic step labels and token filtering."""

    async def test_stream_filters_tool_call_json_chunks(self, monkeypatch):
        """SSE token streaming strictly suppresses tool call argument chunks."""
        registry = get_workflow_registry()
        workflow = registry.get("react_agent")

        # Mock an astream_events sequence where agent first outputs tool call arguments,
        # then tools node runs, then agent outputs conversational text.
        async def mock_events(*args, **kwargs):
            # 1. Start agent node
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}, "data": {}}

            # 2. Tool call chunks from LLM (SHOULD BE FILTERED OUT)
            chunk_tool_1 = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    ToolCallChunk(name="knowledge_base_search", args='{"query": "network"}', id="call_1", index=0)
                ],
            )
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": chunk_tool_1},
            }

            # 3. Tool call chunk wrapped in chunk.message dict/object (SHOULD BE FILTERED OUT)
            chunk_with_message = MagicMock()
            chunk_with_message.content = ""
            chunk_with_message.tool_call_chunks = None
            msg_mock = MagicMock()
            msg_mock.tool_call_chunks = [{"name": "knowledge_base_search"}]
            chunk_with_message.message = msg_mock
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": chunk_with_message},
            }

            # 4. Empty content token (SHOULD BE FILTERED OUT)
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="")},
            }

            # 5. Agent node ends with tool call
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "agent"},
                "data": {
                    "output": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "knowledge_base_search",
                                        "args": {"query": "network"},
                                        "id": "call_1",
                                    }
                                ],
                            )
                        ]
                    }
                },
            }

            # 6. Tools node starts & completes
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "tools"}, "data": {}}
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "tools"},
                "data": {
                    "output": {
                        "messages": [
                            ToolMessage(
                                content="[Source: network_faq.pdf] The network speed is 1Gbps.",
                                name="knowledge_base_search",
                                tool_call_id="call_1",
                            )
                        ]
                    }
                },
            }

            # 7. Agent node starts again for final response
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}, "data": {}}

            # 8. Real conversational tokens (SHOULD BE STREAMED)
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="The network speed ")},
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="is 1Gbps.")},
            }

            # 9. Final chain end
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "agent"},
                "data": {
                    "output": {
                        "messages": [
                            AIMessage(content="The network speed is 1Gbps."),
                        ]
                    }
                },
            }

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_events
        monkeypatch.setattr(workflow, "get_compiled_graph", lambda: mock_graph)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "What is the network speed?", "workflow": "react_agent"},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)

        # Step events verification
        step_events = [e for e in events if e["event"] == "step"]
        step_names = [s["data"]["step"] for s in step_events]
        assert "agent" in step_names
        assert "tools" in step_names

        # Step labels verification from react_agent workflow info
        for s in step_events:
            if s["data"]["step"] == "agent":
                assert s["data"]["label"] == "Reasoning & planning response..."
            elif s["data"]["step"] == "tools":
                assert s["data"]["label"] == "Executing selected tool..."

        # Token events verification
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 2
        assert token_events[0]["data"]["delta"] == "The network speed "
        assert token_events[1]["data"]["delta"] == "is 1Gbps."

        # Confirm zero tool JSON tokens were leaked!
        all_token_text = "".join(t["data"]["delta"] for t in token_events)
        assert "knowledge_base_search" not in all_token_text
        assert "query" not in all_token_text
        assert "{" not in all_token_text

        # Metadata event verification
        metadata_events = [e for e in events if e["event"] == "metadata"]
        assert len(metadata_events) == 1
        meta = metadata_events[0]["data"]
        assert meta["workflow"] == "react_agent"
        assert meta["source_used"] == "vectorstore"
        assert len(meta["citations"]) == 1
        assert meta["citations"][0]["title"] == "network_faq.pdf"

    async def test_stream_unhandled_exception_calls_handle_error_and_yields_error(self, monkeypatch):
        """Unhandled exceptions mid-stream trigger workflow.handle_error and yield an error event."""
        registry = get_workflow_registry()
        workflow = registry.get("fix_single_jev")

        async def mock_events_failing(*args, **kwargs):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "route_question"}, "data": {}}
            raise RuntimeError("Database connection timed out during streaming")

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_events_failing
        monkeypatch.setattr(workflow, "get_compiled_graph", lambda: mock_graph)

        handle_error_spy = MagicMock(wraps=workflow.handle_error)
        monkeypatch.setattr(workflow, "handle_error", handle_error_spy)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Test error handling", "workflow": "fix_single_jev"},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)

        # Verify workflow.handle_error was called
        assert handle_error_spy.called

        # Verify error event emitted
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        err = error_events[0]["data"]
        assert err["code"] == 500
        assert "Database connection timed out during streaming" in err["message"]
