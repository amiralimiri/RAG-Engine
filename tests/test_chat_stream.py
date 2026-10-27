"""Automated streaming tests for POST /api/chat/stream SSE endpoint.

Covers:
  - Boundary & Validation (invalid workflow → 400, invalid question → 422)
  - Header & MIME type assertions (text/event-stream, no-cache, X-Accel-Buffering: no)
  - Wire event sequence & JSON payload correctness (step, token, metadata)
  - Token isolation from routers/graders
  - Feature flag: stream_steps=False suppresses step events
  - Cancellation / disconnection teardown audit (source_used="client_disconnected")
  - Mid-stream runtime error frame emission (event: error)
  - Backward compatibility: sync POST /api/chat remains operational
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import WORKFLOW_ASKS, WORKFLOW_GRAPHS


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
        monkeypatch.setattr("app.api.routes.write_audit", lambda *a, **kw: None)

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
        monkeypatch.setattr("app.api.routes.write_audit", lambda *a, **kw: None)

        resp = client.post(
            "/api/chat/stream",
            json={"question": "How do I reset my password?", "workflow": "fix_single_jev"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "").lower()
        assert "text/event-stream" in ct
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

        audit_records: list[dict[str, Any]] = []
        monkeypatch.setattr("app.api.routes.write_audit", lambda *a, **kw: audit_records.append(kw))

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

        # Verify audit record persistence
        assert len(audit_records) >= 1
        assert audit_records[-1]["source_used"] == "direct"
        assert audit_records[-1]["trace_id"] == "trace-seq-001"

        # Verify callback flush invocation
        mock_handler.flush.assert_called()

    async def test_token_isolation_from_routers_and_graders(self, monkeypatch):
        """Asserts token isolation: router/grader JSON output is never leaked as token deltas."""
        mock_handler = get_mock_handler("trace-iso-002")
        monkeypatch.setattr("app.api.routes.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.api.routes.extract_trace_id", lambda handler: "trace-iso-002")
        monkeypatch.setattr("app.api.routes.write_audit", lambda *a, **kw: None)

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
        monkeypatch.setattr("app.api.routes.write_audit", lambda *a, **kw: None)

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
    """Client dropout mid-stream writes audit record with source_used='client_disconnected'."""

    async def test_disconnect_audit_is_persisted(self, monkeypatch):
        """Simulating client disconnect mid-stream records client_disconnected in query_audit."""
        audit_records: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "app.api.routes.write_audit",
            lambda *a, **kw: audit_records.append(kw),
        )

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

        # Check that audit record was cleanly persisted with client_disconnected
        disconnect_audits = [r for r in audit_records if r.get("source_used") == "client_disconnected"]
        assert len(disconnect_audits) == 1, f"Expected 1 client_disconnected audit, got {audit_records}"
        disc_record = disconnect_audits[0]
        assert disc_record["workflow"] == "fix_single_llm"
        assert disc_record["question"] == "test disconnect inquiry"
        assert disc_record["trace_id"] == "trace-disc-004"
        assert any("Stream aborted by client disconnect" in str(t) for t in disc_record.get("trace", []))

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
        monkeypatch.setattr("app.api.routes.write_audit", lambda *a, **kw: None)

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

        audit_records: list[dict[str, Any]] = []

        def mock_write_audit(*args, **kwargs):
            record = dict(kwargs)
            if args:
                if len(args) >= 1:
                    record["workflow"] = args[0]
                if len(args) >= 2:
                    record["question"] = args[1]
                if len(args) >= 3:
                    record["source_used"] = args[2]
                if len(args) >= 4:
                    record["trace"] = args[3]
            audit_records.append(record)

        monkeypatch.setattr("app.api.routes.write_audit", mock_write_audit)

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
        assert len(audit_records) == 1
        assert audit_records[0]["source_used"] == "knowledge_base"
