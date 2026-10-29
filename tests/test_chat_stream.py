"""Automated streaming tests for POST /api/chat/stream SSE endpoint.

Covers:
  - Boundary & Validation (invalid workflow → 400, invalid question → 422)
  - Header & MIME type assertions (text/event-stream, no-cache, X-Accel-Buffering: no)
  - Wire event sequence & JSON payload correctness (step, token, metadata)
  - Token isolation from routers/graders
  - Feature flag: stream_steps=False suppresses step events
  - Cancellation / disconnection teardown (flush & cleanup)
  - Mid-stream runtime error frame emission (event: error)
  - Backward compatibility: sync POST /api/chat remains operational
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCallChunk
from langchain_core.tools import BaseTool

from app.main import app
from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS
from app.rag.classifiers import FakeClassifierAdapter
from app.rag.workflows import get_workflow_registry
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow


# ── helpers & transports ──────────────────────────────────────────────────


def parse_sse_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Parse raw SSE lines into a list of {'event': str, 'data': dict} objects.

    Handles multi-line data payloads and blank-line delimiters.
    """
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


def get_mock_handler(trace_id: str = "trace-test-999") -> MagicMock:
    h = MagicMock()
    h.last_trace_id = trace_id
    h.flush = MagicMock()
    return h


class CustomByteStream(httpx.AsyncByteStream):
    def __init__(self, aiter_fn, aclose_fn):
        self._aiter_fn = aiter_fn
        self._aclose_fn = aclose_fn

    async def __aiter__(self):
        async for chunk in self._aiter_fn():
            yield chunk

    async def aclose(self):
        await self._aclose_fn()


class DisconnectableASGITransport(httpx.AsyncBaseTransport):
    """ASGI transport that propagates client disconnect when response stream closes."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body_bytes = b"".join([chunk async for chunk in request.stream])
        disconnect_event = asyncio.Event()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port or 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        }

        req_sent = False

        async def receive():
            nonlocal req_sent
            if not req_sent:
                req_sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            await disconnect_event.wait()
            return {"type": "http.disconnect"}

        status_code = None
        headers = []
        response_started = asyncio.Event()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def run_app():
            try:
                async def send(msg):
                    nonlocal status_code, headers
                    if msg["type"] == "http.response.start":
                        status_code = msg["status"]
                        headers = msg.get("headers", [])
                        response_started.set()
                    elif msg["type"] == "http.response.body":
                        await queue.put(msg.get("body", b""))
                        if not msg.get("more_body", False):
                            await queue.put(None)

                await self.asgi_app(scope, receive, send)
            finally:
                await queue.put(None)

        app_task = asyncio.create_task(run_app())
        await response_started.wait()

        async def aiter():
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk

        async def aclose():
            disconnect_event.set()
            await app_task

        return httpx.Response(
            status_code=status_code or 200,
            headers=headers,
            stream=CustomByteStream(aiter, aclose),
        )


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_telemetry_defaults(monkeypatch):
    """Prevent real network telemetry calls during tests."""
    mock_handler = get_mock_handler()
    monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
    monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: getattr(handler, "last_trace_id", "trace-default"))
    monkeypatch.setattr("app.api.routes.flush_telemetry", lambda: None)
    monkeypatch.setattr("app.api.routes.run_online_evaluation_task", AsyncMock())
    monkeypatch.setattr("app.services.prompts.get_langfuse_client", lambda: None)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Boundary & Validation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestBoundaryValidation:
    """Invalid / missing fields → proper HTTP status and error details."""

    def test_unknown_workflow_returns_400(self, client):
        """Request with invalid workflow returns HTTP 400 with descriptive error detail."""
        resp = client.post(
            "/api/chat/stream",
            json={"question": "How do I reset my password?", "workflow": "nonexistent_workflow"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "detail" in data
        assert "Unknown workflow: nonexistent_workflow" in data["detail"]

    def test_empty_question_returns_422(self, client):
        """Empty question string returns HTTP 422 validation error."""
        resp = client.post(
            "/api/chat/stream",
            json={"question": "", "workflow": "fix_single_llm"},
        )
        assert resp.status_code == 422
        errors = resp.json().get("detail", [])
        assert any("question" in str(e) for e in errors)

    def test_question_too_short_returns_422(self, client):
        """Question shorter than min_length=2 returns HTTP 422."""
        resp = client.post(
            "/api/chat/stream",
            json={"question": "a", "workflow": "fix_single_llm"},
        )
        assert resp.status_code == 422
        errors = resp.json().get("detail", [])
        assert any("question" in str(e) for e in errors)

    def test_missing_question_key_returns_422(self, client):
        """Missing `question` key returns HTTP 422."""
        resp = client.post(
            "/api/chat/stream",
            json={"workflow": "fix_single_llm"},
        )
        assert resp.status_code == 422

    def test_missing_workflow_key_defaults_to_fix_single_jev(self, client, monkeypatch):
        """Missing workflow defaults to fix_single_jev without 422 error."""
        async def dummy_events(*args, **kwargs):
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "direct_answer"}, "data": {"chunk": {"content": "ok"}}}
            yield {"event": "on_chain_end", "metadata": {"langgraph_node": "direct_answer"}, "data": {"output": {"source_used": "direct"}}}

        mock_graph = MagicMock()
        mock_graph.astream_events = dummy_events
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_jev", mock_graph)

        resp = client.post(
            "/api/chat/stream",
            json={"question": "Valid inquiry question"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Header & MIME Type Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHeadersAndContentType:
    """Verify standard SSE response headers."""

    def test_streaming_headers(self, client, monkeypatch):
        """Valid request returns HTTP 200 with text/event-stream, no-cache, and X-Accel-Buffering: no."""
        async def dummy_events(*args, **kwargs):
            yield {"event": "on_chain_end", "metadata": {"langgraph_node": "direct_answer"}, "data": {"output": {"source_used": "direct"}}}

        mock_graph = MagicMock()
        mock_graph.astream_events = dummy_events
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_jev", mock_graph)

        resp = client.post(
            "/api/chat/stream",
            json={"question": "How do I reset my password?", "workflow": "fix_single_jev"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "").lower()
        assert "text/event-stream" in ct
        assert "charset=utf-8" in ct
        assert "no-cache" in resp.headers.get("cache-control", "").lower()
        assert resp.headers.get("x-accel-buffering") == "no"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Wire Event Sequence & JSON Payload Tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestWireEventSequence:
    """Parse streamed lines and verify discrete SSE events with valid JSON payloads."""

    async def _mock_astream_events(self, *args, **kwargs):
        """Simulate realistic sequence of LangGraph astream_events v2."""
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "route_question"}, "data": {}}
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "retrieve_kb"}, "data": {}}
        yield {"event": "on_chain_end", "metadata": {"langgraph_node": "retrieve_kb"}, "data": {"output": {"trace": ["route_question", "retrieve_kb"]}}}
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "direct_answer"}, "data": {"chunk": {"content": "Hello"}}}
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "direct_answer"}, "data": {"chunk": {"content": " world!\n\nHere is code:\n  return 42"}}}
        yield {"event": "on_chain_end", "metadata": {"langgraph_node": "direct_answer"}, "data": {"output": {"source_used": "direct", "citations": [{"title": "guide.md"}], "current_query": "rewritten query"}}}

    async def test_event_sequence_and_payloads(self, monkeypatch):
        """Verify step, token, and metadata events are formatted as valid JSON data blocks."""
        mock_handler = get_mock_handler("trace-seq-001")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-seq-001")

        mock_graph = MagicMock()
        mock_graph.astream_events = self._mock_astream_events
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_llm", mock_graph)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "test sequence query", "workflow": "fix_single_llm", "stream_steps": True},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)
        event_types = [e["event"] for e in events]

        # Verify discrete SSE event types
        assert "step" in event_types
        assert "token" in event_types
        assert "metadata" in event_types

        # Verify step payloads
        step_events = [e for e in events if e["event"] == "step"]
        assert len(step_events) >= 1
        for s in step_events:
            d = s["data"]
            assert isinstance(d, dict)
            assert "step" in d
            assert "label" in d
            assert "timestamp" in d
            assert isinstance(d["timestamp"], (int, float))

        # Verify token payloads parse as valid JSON {"delta": "..."}
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 2
        for t in token_events:
            d = t["data"]
            assert isinstance(d, dict)
            assert "delta" in d
            assert isinstance(d["delta"], str)

        # Verify whitespace preservation across chunks
        full_text = "".join(t["data"]["delta"] for t in token_events)
        expected_text = "Hello world!\n\nHere is code:\n  return 42"
        assert full_text == expected_text

        # Verify terminal metadata payload
        meta_events = [e for e in events if e["event"] == "metadata"]
        assert len(meta_events) == 1
        meta = meta_events[0]["data"]
        assert isinstance(meta, dict)
        assert meta["trace_id"] == "trace-seq-001"
        assert meta["source_used"] == "direct"
        assert meta["workflow"] == "fix_single_llm"
        assert isinstance(meta["trace"], list)
        assert meta["citations"] == [{"title": "guide.md"}]
        assert meta["rewritten_query"] == "rewritten query"

        # Verify callback flush invocation
        mock_handler.flush.assert_called()

    async def test_streaming_triggers_online_evaluation_task(self, monkeypatch):
        """Verify streaming chat completion dispatches run_online_evaluation_task with trace_id and contexts."""
        mock_handler = get_mock_handler("trace-eval-stream-1")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-eval-stream-1")

        eval_task_called = asyncio.Event()
        captured_kwargs = {}

        async def fake_eval_task(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            eval_task_called.set()

        monkeypatch.setattr("app.api.routes.run_online_evaluation_task", fake_eval_task)

        async def mock_events(*args, **kwargs):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "retrieve_kb"}, "data": {}}
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "retrieve_kb"},
                "data": {"output": {"retrieved_contexts": ["KB Context doc 1"]}},
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {"chunk": {"content": "Final streamed answer"}},
            }
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {
                    "output": {
                        "answer": "Final streamed answer",
                        "source_used": "knowledge_base",
                        "citations": [],
                        "retrieved_contexts": ["KB Context doc 1"],
                    }
                },
            }

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_events
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_llm", mock_graph)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Where is the documentation?", "workflow": "fix_single_llm"},
            ) as resp:
                assert resp.status_code == 200
                _ = [line async for line in resp.aiter_lines()]

        await asyncio.wait_for(eval_task_called.wait(), timeout=2.0)
        assert captured_kwargs["trace_id"] == "trace-eval-stream-1"
        assert captured_kwargs["question"] == "Where is the documentation?"
        assert captured_kwargs["answer"] == "Final streamed answer"
        assert captured_kwargs["source_used"] == "knowledge_base"
        assert captured_kwargs["retrieved_contexts"] == ["KB Context doc 1"]

        # Ensure background task reference is cleaned up after completion
        from app.api.routes import _background_tasks
        # Give event loop a cycle for task done callback
        await asyncio.sleep(0.05)
        assert len(_background_tasks) == 0

    async def test_non_streaming_fallback_node_emits_static_token(self, monkeypatch):
        """When reaching insufficient fallback node with static answer, an event: token is synthesized."""
        mock_handler = get_mock_handler("trace-insufficient-001")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-insufficient-001")

        async def mock_insufficient_stream(*args, **kwargs):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "route_question"}, "data": {}}
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "insufficient"}, "data": {}}
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "insufficient"},
                "data": {
                    "output": {
                        "answer": "I couldn't find enough reliable evidence in the company knowledge base.",
                        "source_used": "insufficient_evidence",
                        "trace": ["Stopped → insufficient reliable evidence"],
                    }
                },
            }

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_insufficient_stream
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_llm", mock_graph)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "obscure query", "workflow": "fix_single_llm", "stream_steps": True},
            ) as resp:
                assert resp.status_code == 200
                assert "charset=utf-8" in resp.headers.get("content-type", "").lower()
                lines = [line async for line in resp.aiter_lines()]

        parsed = parse_sse_lines(lines)
        tokens = [p["data"]["delta"] for p in parsed if p["event"] == "token"]
        assert len(tokens) == 1
        assert "I couldn't find enough reliable evidence" in tokens[0]

        metas = [p["data"] for p in parsed if p["event"] == "metadata"]
        assert len(metas) == 1
        assert metas[0]["source_used"] == "insufficient_evidence"

    async def test_token_isolation_from_routers_and_graders(self, monkeypatch):
        """Asserts token isolation: router/grader JSON output is never leaked as token deltas."""
        mock_handler = get_mock_handler("trace-iso-002")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-iso-002")

        async def mock_events(*args, **kwargs):
            # Router node emits structured json chunk -> must be ignored
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "route_question"}, "data": {"chunk": {"content": '{"route": "kb"}'}}}
            # Grader node emits binary score json chunk -> must be ignored
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "grade_kb"}, "data": {"chunk": {"content": '{"binary_score": "yes"}'}}}
            # Whitelisted generation node emits real token delta -> must be yielded
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "generate_from_kb"}, "data": {"chunk": {"content": "Verified knowledge base content."}}}
            yield {"event": "on_chain_end", "metadata": {"langgraph_node": "generate_from_kb"}, "data": {"output": {"source_used": "knowledge_base"}}}

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_events
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_llm", mock_graph)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream("POST", "/api/chat/stream", json={"question": "test isolation", "workflow": "fix_single_llm"}) as resp:
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 1
        token_delta = token_events[0]["data"]["delta"]
        assert token_delta == "Verified knowledge base content."
        assert "route" not in token_delta
        assert "binary_score" not in token_delta


# ═══════════════════════════════════════════════════════════════════════════
# 4. Feature Flag: stream_steps=False
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestStreamStepsFeatureFlag:
    """Feature flag validation: stream_steps=False suppresses event: step frames."""

    async def _mock_astream_events(self, *args, **kwargs):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "route_question"}, "data": {}}
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "retrieve_kb"}, "data": {}}
        yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "direct_answer"}, "data": {"chunk": {"content": "Direct reply"}}}
        yield {"event": "on_chain_end", "metadata": {"langgraph_node": "direct_answer"}, "data": {"output": {"source_used": "direct"}}}

    async def test_no_step_events_when_stream_steps_false(self, monkeypatch):
        """When stream_steps=False, no event: step frames are emitted before tokens."""
        mock_handler = get_mock_handler("trace-nostep-003")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-nostep-003")

        mock_graph = MagicMock()
        mock_graph.astream_events = self._mock_astream_events
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_llm", mock_graph)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "test no steps", "workflow": "fix_single_llm", "stream_steps": False},
            ) as resp:
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)
        event_types = [e["event"] for e in events]

        assert "step" not in event_types
        assert "token" in event_types
        assert "metadata" in event_types


# ═══════════════════════════════════════════════════════════════════════════
# 5. Cancellation / Disconnection Teardown
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestClientDisconnection:
    """Client dropout mid-stream triggers graceful teardown and flush."""

    async def test_disconnect_triggers_telemetry_flush(self, monkeypatch):
        """Simulating client disconnect mid-stream invokes callback flush during teardown."""
        mock_handler = get_mock_handler("trace-disc-004")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-disc-004")

        async def mock_astream_events_long(*args, **kwargs):
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "direct_answer"}, "data": {"chunk": {"content": "First token"}}}
            # Simulate long wait where client drops out
            await asyncio.sleep(10.0)
            yield {"event": "on_chain_end", "metadata": {"langgraph_node": "direct_answer"}, "data": {"output": {"source_used": "direct"}}}

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_astream_events_long
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_llm", mock_graph)

        transport = DisconnectableASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "test disconnect inquiry", "workflow": "fix_single_llm"},
            ) as resp:
                async for line in resp.aiter_lines():
                    if "First token" in line:
                        # Break early to simulate client connection abort
                        break

        # Callback flush is invoked during disconnect teardown
        mock_handler.flush.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# 6. Mid-stream Error Event
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMidStreamError:
    """Runtime errors during streaming emit event: error with code 500."""

    async def test_mid_stream_error_emits_error_event(self, monkeypatch):
        mock_handler = get_mock_handler("trace-err-005")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-err-005")

        async def mock_events_with_error(*args, **kwargs):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "route_question"}, "data": {}}
            raise RuntimeError("Database connection lost during stream")

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_events_with_error
        monkeypatch.setitem(WORKFLOW_GRAPHS, "fix_single_llm", mock_graph)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "test error handling", "workflow": "fix_single_llm"},
            ) as resp:
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        err = error_events[0]["data"]
        assert isinstance(err, dict)
        assert err["code"] == 500
        assert "Database connection lost during stream" in err["message"]


# ═══════════════════════════════════════════════════════════════════════════
# 7. Backward Compatibility — sync POST /api/chat
# ═══════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Synchronous POST /api/chat continues functioning identically."""

    def test_sync_chat_endpoint_works(self, client, monkeypatch):
        """Standard POST /api/chat returns HTTP 200 with complete response dict."""
        mock_handler = get_mock_handler("trace-bc-006")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-bc-006")

        def mock_ask(question, **kwargs):
            return {
                "answer": "Backward compatible synchronous answer.",
                "source_used": "knowledge_base",
                "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
                "citations": [{"title": "network_guide.pdf"}],
                "current_query": question,
            }

        monkeypatch.setitem(WORKFLOW_ASKS, "fix_single_llm", mock_ask)

        resp = client.post("/api/chat", json={"question": "How do I configure VPN?", "workflow": "fix_single_llm"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow"] == "fix_single_llm"
        assert data["answer"] == "Backward compatible synchronous answer."
        assert data["trace_id"] == "trace-bc-006"
        assert data["source_used"] == "knowledge_base"
        assert data["citations"] == [{"title": "network_guide.pdf"}]


# ═══════════════════════════════════════════════════════════════════════════
# 8. End-to-End Multi-Workflow Streaming Verification (Ticket 08)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMultiWorkflowStreamingE2E:
    """End-to-end multi-workflow verification for streaming SSE endpoint."""

    async def test_stream_switching_between_jev_and_react(self, monkeypatch):
        """Verify dynamic switching between fix_single_jev and react_agent via /api/chat/stream."""
        registry = get_workflow_registry()
        jev_wf = registry.get("fix_single_jev")
        react_wf = registry.get("react_agent")

        mock_handler = get_mock_handler("trace-switch-001")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-switch-001")

        # 1. Stream fix_single_jev
        async def mock_jev_events(*args, **kwargs):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "route_question"}, "data": {}}
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "retrieve_kb"}, "data": {}}
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "generate_from_kb"}, "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {"chunk": {"content": "JEV answer stream"}},
            }
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {
                    "output": {
                        "answer": "JEV answer stream",
                        "source_used": "vectorstore",
                        "citations": [{"title": "jev_guide.pdf"}],
                        "trace": ["route_question", "retrieve_kb", "generate_from_kb"],
                    }
                },
            }

        mock_jev_graph = MagicMock()
        mock_jev_graph.astream_events = mock_jev_events
        monkeypatch.setattr(jev_wf, "get_compiled_graph", lambda: mock_jev_graph)

        # 2. Stream react_agent
        async def mock_react_events(*args, **kwargs):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}, "data": {}}
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "tools"}, "data": {}}
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}, "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="ReAct agent reply")},
            }
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "agent"},
                "data": {
                    "output": {
                        "messages": [
                            ToolMessage(content="[Source: react_tool.pdf] Tool output", name="knowledge_base_search", tool_call_id="call_1"),
                            AIMessage(content="ReAct agent reply"),
                        ]
                    }
                },
            }

        mock_react_graph = MagicMock()
        mock_react_graph.astream_events = mock_react_events
        monkeypatch.setattr(react_wf, "get_compiled_graph", lambda: mock_react_graph)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            # Request 1: fix_single_jev
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Question for JEV", "workflow": "fix_single_jev"},
            ) as resp1:
                assert resp1.status_code == 200
                lines1 = [line async for line in resp1.aiter_lines()]

            events1 = parse_sse_lines(lines1)
            steps1 = [e["data"]["step"] for e in events1 if e["event"] == "step"]
            assert "route_question" in steps1
            assert "retrieve_kb" in steps1
            assert "generate_from_kb" in steps1

            tokens1 = "".join(e["data"]["delta"] for e in events1 if e["event"] == "token")
            assert tokens1 == "JEV answer stream"

            meta1 = [e["data"] for e in events1 if e["event"] == "metadata"][0]
            assert meta1["workflow"] == "fix_single_jev"
            assert meta1["source_used"] == "vectorstore"

            # Request 2: react_agent
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Question for ReAct", "workflow": "react_agent"},
            ) as resp2:
                assert resp2.status_code == 200
                lines2 = [line async for line in resp2.aiter_lines()]

            events2 = parse_sse_lines(lines2)
            steps2 = [e["data"]["step"] for e in events2 if e["event"] == "step"]
            assert "agent" in steps2
            assert "tools" in steps2

            labels2 = {e["data"]["step"]: e["data"]["label"] for e in events2 if e["event"] == "step"}
            assert labels2["agent"] == "Reasoning & planning response..."
            assert labels2["tools"] == "Executing selected tool..."

            tokens2 = "".join(e["data"]["delta"] for e in events2 if e["event"] == "token")
            assert tokens2 == "ReAct agent reply"

            meta2 = [e["data"] for e in events2 if e["event"] == "metadata"][0]
            assert meta2["workflow"] == "react_agent"

    async def test_react_agent_strict_conversational_tokens_and_step_events(self, monkeypatch):
        """Streaming tokens for react_agent verified to contain only conversational text, with tool invocations emitting step events."""
        registry = get_workflow_registry()
        workflow = registry.get("react_agent")

        mock_handler = get_mock_handler("trace-react-stream-002")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-react-stream-002")

        async def mock_events(*args, **kwargs):
            # Step 1: Agent starts
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}, "data": {}}

            # Model emits tool call chunks (must be filtered out)
            tool_chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    ToolCallChunk(name="knowledge_base_search", args='{"query": "vpn password"}', id="call_abc", index=0)
                ],
            )
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": tool_chunk},
            }

            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "agent"},
                "data": {
                    "output": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[{"name": "knowledge_base_search", "args": {"query": "vpn password"}, "id": "call_abc"}],
                            )
                        ]
                    }
                },
            }

            # Step 2: Tools node starts
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "tools"}, "data": {}}
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "tools"},
                "data": {
                    "output": {
                        "messages": [
                            ToolMessage(
                                content="[Source: password_policy.pdf] Passwords expire every 90 days.",
                                name="knowledge_base_search",
                                tool_call_id="call_abc",
                            )
                        ]
                    }
                },
            }

            # Step 3: Agent resumes and emits natural language tokens
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}, "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="Your corporate password ")},
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="expires every 90 days.")},
            }
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "agent"},
                "data": {
                    "output": {
                        "messages": [
                            AIMessage(content="Your corporate password expires every 90 days."),
                        ]
                    }
                },
            }

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_events
        monkeypatch.setattr(workflow, "get_compiled_graph", lambda: mock_graph)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "When does password expire?", "workflow": "react_agent"},
            ) as resp:
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)

        # 1. Step events must include agent and tools
        step_events = [e for e in events if e["event"] == "step"]
        steps = [s["data"]["step"] for s in step_events]
        assert "agent" in steps
        assert "tools" in steps

        # 2. Token events must contain ONLY conversational text
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 2
        full_text = "".join(t["data"]["delta"] for t in token_events)
        assert full_text == "Your corporate password expires every 90 days."

        # 3. Assert zero leak of JSON tool arguments or schema
        assert "{" not in full_text
        assert "}" not in full_text
        assert "knowledge_base_search" not in full_text
        assert "query" not in full_text
        assert "call_abc" not in full_text

        # 4. Terminal metadata
        meta = [e for e in events if e["event"] == "metadata"][0]["data"]
        assert meta["workflow"] == "react_agent"
        assert meta["citations"][0]["title"] == "password_policy.pdf"

    async def test_streaming_multi_turn_session_persistence(self, monkeypatch):
        """Multi-turn session persistence verified end-to-end via HTTP streaming requests sharing session_id."""
        registry = get_workflow_registry()
        workflow = registry.get("react_agent")

        mock_handler = get_mock_handler("trace-stream-multiturn")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-stream-multiturn")

        captured_configs = []

        async def mock_events(*args, **kwargs):
            if "config" in kwargs:
                captured_configs.append(kwargs["config"])
            elif len(args) > 1 and isinstance(args[1], dict):
                captured_configs.append(args[1])
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}, "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="Acknowledged.")},
            }
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "agent"},
                "data": {"output": {"messages": [AIMessage(content="Acknowledged.")]}},
            }

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_events
        monkeypatch.setattr(workflow, "get_compiled_graph", lambda: mock_graph)

        shared_session_id = "test-persistent-stream-session-88"

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            # Turn 1
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "question": "My favorite editor is Vim.",
                    "workflow": "react_agent",
                    "session_id": shared_session_id,
                },
            ) as resp1:
                assert resp1.status_code == 200
                _ = [line async for line in resp1.aiter_lines()]

            # Turn 2
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "question": "What is my favorite editor?",
                    "workflow": "react_agent",
                    "session_id": shared_session_id,
                },
            ) as resp2:
                assert resp2.status_code == 200
                _ = [line async for line in resp2.aiter_lines()]

        # Both invocations must share the exact same thread_id in configurable
        assert len(captured_configs) == 2
        assert captured_configs[0].get("configurable", {}).get("thread_id") == shared_session_id
        assert captured_configs[1].get("configurable", {}).get("thread_id") == shared_session_id


# ═══════════════════════════════════════════════════════════════════════════
# 9. Unified BaseTool Streaming Regression Tests (ADR-0008 Ticket 05)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestUnifiedBaseToolStreamingRegression:
    """End-to-end streaming regression verifying the unified BaseTool abstraction seam."""

    async def test_streaming_with_injected_kb_tool_dual_output_artifact(self, monkeypatch):
        """Verify SSE step events, token streaming, and terminal metadata citations from injected kb_tool artifact."""
        fake_llm = FakeListChatModel(responses=["Follow the official VPN guidelines to connect."])
        monkeypatch.setattr("app.rag.engine.llm", lambda: fake_llm)

        eval_task_called = asyncio.Event()
        captured_eval = {}

        async def fake_eval(**kwargs):
            nonlocal captured_eval
            captured_eval = kwargs
            eval_task_called.set()

        monkeypatch.setattr("app.api.routes.run_online_evaluation_task", fake_eval)

        kb_doc = Document(page_content="Official company VPN setup guide.", metadata={"source": "docs/network/vpn_setup.md"})
        kb_tool = MagicMock(spec=BaseTool)
        kb_tool.ainvoke = AsyncMock(
            return_value=(
                "[Source: docs/network/vpn_setup.md]\nOfficial company VPN setup guide.",
                {
                    "documents": [kb_doc],
                    "citations": [{"title": "vpn_setup.md", "url": "", "type": "private_kb"}],
                },
            )
        )

        classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
        workflow = SelfCorrectiveRagWorkflow(
            id="stream_kb_seam_wf",
            name="Stream KB Seam Workflow",
            classifier=classifier,
            kb_tool=kb_tool,
        )

        registry = get_workflow_registry()
        registry.register(workflow)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "How do I setup VPN?", "workflow": "stream_kb_seam_wf", "stream_steps": True},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)

        # 1. Step transitions
        step_events = [e for e in events if e["event"] == "step"]
        steps = [s["data"]["step"] for s in step_events]
        assert "route_question" in steps
        assert "retrieve_kb" in steps
        assert "grade_kb" in steps
        assert "generate_from_kb" in steps

        # 2. Token delta delivery
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) >= 1
        full_text = "".join(t["data"]["delta"] for t in token_events)
        assert "Follow the official VPN guidelines to connect." in full_text

        # 3. Terminal metadata directly reflecting tool artifact
        meta_events = [e for e in events if e["event"] == "metadata"]
        assert len(meta_events) == 1
        meta = meta_events[0]["data"]
        assert meta["workflow"] == "stream_kb_seam_wf"
        assert meta["source_used"] == "vectorstore"
        assert meta["citations"] == [{"title": "vpn_setup.md", "url": "", "type": "private_kb"}]
        assert any("Private KB retrieval" in t for t in meta["trace"])

        # 4. Async online evaluation received retrieved_contexts from artifact
        await asyncio.wait_for(eval_task_called.wait(), timeout=2.0)
        assert captured_eval["retrieved_contexts"] == ["Official company VPN setup guide."]
        assert captured_eval["source_used"] == "vectorstore"

    async def test_streaming_with_injected_web_tool_dual_output_artifact(self, monkeypatch):
        """Verify SSE streaming flow when falling back to injected web_tool, extracting citations and context from artifact."""
        fake_llm = FakeListChatModel(responses=["Install Docker via official apt repository."])
        monkeypatch.setattr("app.rag.engine.llm", lambda: fake_llm)

        eval_task_called = asyncio.Event()
        captured_eval = {}

        async def fake_eval(**kwargs):
            nonlocal captured_eval
            captured_eval = kwargs
            eval_task_called.set()

        monkeypatch.setattr("app.api.routes.run_online_evaluation_task", fake_eval)

        kb_tool = MagicMock(spec=BaseTool)
        kb_tool.ainvoke = AsyncMock(return_value=("No internal KB docs found", {"documents": [], "citations": []}))

        web_tool = MagicMock(spec=BaseTool)
        web_tool.ainvoke = AsyncMock(
            return_value=(
                "Tavily search answer: Run apt-get install docker-ce.",
                {
                    "citations": [{"title": "Docker Install Docs", "url": "https://docs.docker.com/engine/install", "type": "web"}],
                    "retrieved_contexts": ["Title: Docker Install Docs\nURL: https://docs.docker.com/engine/install\nContent: Run apt-get install docker-ce."],
                },
            )
        )

        classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="weak", web_grade="good")
        workflow = SelfCorrectiveRagWorkflow(
            id="stream_web_seam_wf",
            name="Stream Web Seam Workflow",
            classifier=classifier,
            kb_tool=kb_tool,
            web_tool=web_tool,
        )

        registry = get_workflow_registry()
        registry.register(workflow)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "How do I install Docker?", "workflow": "stream_web_seam_wf", "stream_steps": True},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)

        # 1. Step transitions include web fallback sequence
        step_events = [e for e in events if e["event"] == "step"]
        steps = [s["data"]["step"] for s in step_events]
        assert "retrieve_kb" in steps
        assert "search_web" in steps
        assert "grade_web" in steps
        assert "generate_from_web" in steps

        # 2. Token delivery
        tokens = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
        assert "Install Docker via official apt repository." in tokens

        # 3. Terminal metadata contains web citations from tool artifact
        meta = [e["data"] for e in events if e["event"] == "metadata"][0]
        assert meta["workflow"] == "stream_web_seam_wf"
        assert meta["source_used"] == "web_search"
        assert meta["citations"] == [{"title": "Docker Install Docs", "url": "https://docs.docker.com/engine/install", "type": "web"}]
        assert any("Web fallback" in t for t in meta["trace"])

        # 4. Evaluation context contains web retrieved_contexts
        await asyncio.wait_for(eval_task_called.wait(), timeout=2.0)
        assert captured_eval["retrieved_contexts"] == ["Title: Docker Install Docs\nURL: https://docs.docker.com/engine/install\nContent: Run apt-get install docker-ce."]
        assert captured_eval["source_used"] == "web_search"

    async def test_streaming_transparent_tool_error_propagation(self, monkeypatch):
        """Verify that provider exception raised inside an async tool node propagates and yields SSE error frame."""
        kb_tool = MagicMock(spec=BaseTool)
        kb_tool.ainvoke = AsyncMock(side_effect=ConnectionError("Vectorstore Pinecone connection timed out"))

        classifier = FakeClassifierAdapter(route_decision="kb")
        workflow = SelfCorrectiveRagWorkflow(
            id="stream_err_seam_wf",
            name="Stream Error Seam Workflow",
            classifier=classifier,
            kb_tool=kb_tool,
        )

        registry = get_workflow_registry()
        registry.register(workflow)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Trigger provider timeout", "workflow": "stream_err_seam_wf"},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        err_payload = error_events[0]["data"]
        assert err_payload["code"] == 500
        assert "Vectorstore Pinecone connection timed out" in err_payload["message"]

    async def test_streaming_legacy_retriever_adapter_compatibility(self, monkeypatch):
        """Verify that passing legacy retriever adapter into SelfCorrectiveRagWorkflow streams end-to-end flawlessly."""
        fake_llm = FakeListChatModel(responses=["Legacy adapter streaming answer."])
        monkeypatch.setattr("app.rag.engine.llm", lambda: fake_llm)

        mock_retriever = MagicMock()
        legacy_doc = Document(page_content="Legacy knowledge doc.", metadata={"source": "docs/legacy_stream.pdf"})
        mock_retriever.ainvoke = AsyncMock(return_value=[legacy_doc])
        mock_retriever.invoke = MagicMock(return_value=[legacy_doc])

        classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
        workflow = SelfCorrectiveRagWorkflow(
            id="stream_legacy_adapter_wf",
            name="Stream Legacy Adapter Workflow",
            classifier=classifier,
            retriever=mock_retriever,
        )

        registry = get_workflow_registry()
        registry.register(workflow)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Legacy adapter question", "workflow": "stream_legacy_adapter_wf", "stream_steps": True},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines()]

        events = parse_sse_lines(lines)
        steps = [s["data"]["step"] for s in events if s["event"] == "step"]
        assert "retrieve_kb" in steps

        tokens = "".join(t["data"]["delta"] for t in events if t["event"] == "token")
        assert "Legacy adapter streaming answer." in tokens

        meta = [e["data"] for e in events if e["event"] == "metadata"][0]
        assert meta["workflow"] == "stream_legacy_adapter_wf"
        assert meta["source_used"] == "vectorstore"
        assert meta["citations"] == [{"title": "legacy_stream.pdf", "url": "", "type": "private_kb"}]
