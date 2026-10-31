"""End-to-End Frontend & Backend Streaming Integration Tests (Ticket 7).

Validates end-to-end integration across:
1. Semantic HTML shell delivery of streaming-specific elements
   (.thinking-status, .thinking-workflow, #thinkingBubble, aria-live attributes)
2. CSS rule sets for streaming states, .interrupted-badge, and warning indicators
3. Client JavaScript stream reader/parser exports and runtime guardrails
   (streamChat, readEventStream, parseSSEFrame, scheduleProgressiveRender, DOMPurify)
4. Contract & metadata parity between POST /api/chat/stream and legacy POST /api/chat
5. Error contract parity for input validation and unknown workflows
6. Node.js browser-runtime test suite execution for streaming parser and UI lifecycle
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def mock_online_evaluation_defaults(monkeypatch):
    """Prevent unawaited background evaluation tasks during frontend streaming tests."""
    monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", AsyncMock())


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


def parse_sse_events(lines: list[str]) -> list[dict[str, Any]]:
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


# ============================================================================
# 1. HTML Shell & Streaming-Specific DOM Elements Delivery
# ============================================================================


class TestStreamingHtmlDelivery:
    """Verifies that templates/index.html delivers all streaming-specific DOM elements."""

    def test_html_shell_delivers_streaming_specific_elements(self, client):
        """Validate that templates/index.html contains #thinkingBubble, .thinking-status, and .thinking-workflow."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        html = response.text

        # 1. In-Feed Thinking Placeholder Bubble with accessibility
        assert 'id="thinkingBubble"' in html, "Missing #thinkingBubble container in index.html"
        assert 'class="message assistant thinking-bubble hidden"' in html or 'thinking-bubble' in html
        assert 'aria-live="polite"' in html, "#thinkingBubble must have aria-live='polite' for accessibility"

        # 2. Streaming workflow and dynamic step status badges
        assert 'class="thinking-workflow"' in html, "Missing .thinking-workflow element in index.html"
        assert 'id="thinkingWorkflow"' in html, "Missing #thinkingWorkflow ID in index.html"
        assert 'class="thinking-status hidden"' in html or 'thinking-status' in html, (
            "Missing .thinking-status element in index.html"
        )

        # 3. Dynamic visual indicator and elapsed ticker
        assert 'class="radar-pulse"' in html, "Missing .radar-pulse radar wave element in index.html"
        assert 'id="thinkingTimer"' in html, "Missing #thinkingTimer elapsed timer element in index.html"
        assert 'class="thinking-timer"' in html, "Missing .thinking-timer class in index.html"

    def test_html_shell_script_loading_pipeline(self, client):
        """Validate vendored scripts and core app.js are declared before </body> in correct order."""
        response = client.get("/")
        assert response.status_code == 200
        html = response.text

        body_close = html.lower().rfind("</body>")
        assert body_close != -1

        marked_pos = html.find("/static/vendor/marked.min.js")
        purify_pos = html.find("/static/vendor/purify.min.js")
        highlight_pos = html.find("/static/vendor/highlight.min.js")
        app_pos = html.find("/static/js/app.js")

        assert marked_pos != -1, "Missing marked.min.js"
        assert purify_pos != -1, "Missing purify.min.js"
        assert highlight_pos != -1, "Missing highlight.min.js"
        assert app_pos != -1, "Missing app.js"

        # Loading order: marked -> purify -> highlight -> app.js < </body>
        assert marked_pos < purify_pos < highlight_pos < app_pos < body_close


# ============================================================================
# 2. CSS Rule Sets for Streaming & Interruption Indicators
# ============================================================================


class TestStreamingCssDelivery:
    """Verifies that static/css/style.css contains rule sets for streaming and interrupted states."""

    def test_css_contains_interrupted_badge_and_warning_rules(self, client):
        """Validate that static/css/style.css contains rule sets for .interrupted-badge and warning indicators."""
        response = client.get("/static/css/style.css")
        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")
        css = response.text

        # 1. Interrupted and warning badge selectors
        assert ".interrupted-badge" in css, "Missing .interrupted-badge selector in style.css"
        assert ".warning-badge" in css, "Missing .warning-badge selector in style.css"
        assert ".badge-interrupted" in css, "Missing .badge-interrupted selector in style.css"

        # 2. Warning indicator styling properties
        assert "239, 68, 68" in css or "#ef4444" in css or "#f87171" in css, (
            "Expected red warning highlight colors for interrupted badge in style.css"
        )
        assert "inline-flex" in css, "Expected inline-flex display for badge alignment"

    def test_css_contains_thinking_state_rules(self, client):
        """Validate that static/css/style.css contains styling for thinking state elements."""
        response = client.get("/static/css/style.css")
        assert response.status_code == 200
        css = response.text

        assert ".thinking-bubble" in css
        assert ".thinking-content" in css
        assert ".thinking-workflow" in css
        assert ".thinking-status" in css
        assert ".thinking-timer" in css
        assert ".radar-pulse" in css


# ============================================================================
# 3. Frontend Client Stream Functions & Wire Protocol Exports
# ============================================================================


class TestClientStreamingExports:
    """Verifies that static/js/app.js exposes stream client functions and wire protocol handlers."""

    def test_app_js_exposes_all_stream_client_functions(self):
        """Validate that static/js/app.js exposes streamChat, readEventStream, parseSSEFrame, and scheduleProgressiveRender."""
        app_js_path = BASE_DIR / "static" / "js" / "app.js"
        assert app_js_path.is_file(), "static/js/app.js must exist"
        content = app_js_path.read_text(encoding="utf-8")

        # Function definitions
        assert "async function* readEventStream" in content
        assert "async function* streamChat" in content
        assert "function parseSSEFrame" in content
        assert "function scheduleProgressiveRender" in content
        assert "function flushProgressiveRender" in content
        assert "function updateThinkingStep" in content
        assert "function createStreamingAssistantBubble" in content
        assert "function abortCurrentStream" in content

        # Exports verification (both window.App and module.exports)
        for fn_name in [
            "streamChat",
            "readEventStream",
            "parseSSEFrame",
            "scheduleProgressiveRender",
            "flushProgressiveRender",
            "updateThinkingStep",
            "createStreamingAssistantBubble",
            "abortCurrentStream",
        ]:
            assert fn_name in content, f"Expected function '{fn_name}' to be exposed in app.js"

    def test_app_js_wire_protocol_and_streaming_mechanisms(self):
        """Validate wire streaming endpoints, reader lock management, and 4 SSE event types."""
        app_js_path = BASE_DIR / "static" / "js" / "app.js"
        content = app_js_path.read_text(encoding="utf-8")

        # Dispatches to streaming endpoint
        assert "/api/chat/stream" in content

        # Native stream reading and lock release
        assert "response.body.getReader()" in content
        assert 'new TextDecoder("utf-8")' in content
        assert "reader.releaseLock()" in content

        # Event taxonomy dispatch: step, token, metadata, error
        assert 'case "step":' in content or "event === 'step'" in content or 'event === "step"' in content
        assert 'case "token":' in content or "event === 'token'" in content or 'event === "token"' in content
        assert 'case "metadata":' in content or "event === 'metadata'" in content or 'event === "metadata"' in content
        assert 'case "error":' in content or "event === 'error'" in content or 'event === "error"' in content

    def test_app_js_xss_sanitization_and_progressive_rendering(self):
        """Validate that progressive rendering enforces markdown compilation and DOMPurify sanitization."""
        app_js_path = BASE_DIR / "static" / "js" / "app.js"
        content = app_js_path.read_text(encoding="utf-8")

        # Markdown parsing + DOMPurify sanitization
        assert "marked.parse" in content
        assert "DOMPurify.sanitize" in content
        assert "requestAnimationFrame" in content

        # Interrupted recovery persistence
        assert "rag_session_" in content
        assert "[Interrupted / Incomplete]" in content
        assert "badge-interrupted" in content or "interrupted-badge" in content


# ============================================================================
# 4. End-to-End Chat Streaming Contract Parity vs Legacy POST /api/chat
# ============================================================================


class TestStreamingContractParity:
    """Verifies that POST /api/chat/stream yields matching metadata and contracts compared to legacy POST /api/chat."""

    @pytest.mark.parametrize("workflow", ["fix_single_llm", "fix_single_jev"])
    def test_e2e_streaming_vs_sync_contract_parity(self, client, monkeypatch, register_mock_workflow, workflow):
        """Verify that streaming via POST /api/chat/stream yields identical metadata and contracts compared to POST /api/chat."""
        test_trace_id = "trace-e2e-parity-12345"
        mock_handler = MagicMock()
        mock_handler.last_trace_id = test_trace_id
        mock_handler.flush = MagicMock()

        monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: test_trace_id)
        monkeypatch.setattr("app.rag.workflows.runner.flush_telemetry", lambda: None)

        test_answer = (
            "To connect to the corporate VPN, authenticate via Okta MFA and launch GlobalProtect."
        )
        test_source = "vectorstore"
        test_trace = ["route_question", "retrieve_kb", "grade_documents", "generate_from_kb"]
        test_citations = [
            {"title": "VPN Handbook", "url": "https://intranet.example.com/vpn-guide.pdf"}
        ]
        test_rewritten = "How do I connect to the corporate VPN?"

        # Setup streaming graph mock emitting steps, tokens, and final completion
        async def mock_astream_events(initial_state, version="v2", config=None):
            # Step events
            yield {
                "event": "on_chain_start",
                "metadata": {"langgraph_node": "route_question"},
                "data": {},
            }
            yield {
                "event": "on_chain_start",
                "metadata": {"langgraph_node": "retrieve_kb"},
                "data": {},
            }
            yield {
                "event": "on_chain_start",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {},
            }

            # Progressive token deltas
            chunks = [
                "To connect to the corporate VPN, ",
                "authenticate via Okta MFA ",
                "and launch GlobalProtect.",
            ]
            for c in chunks:
                yield {
                    "event": "on_chat_model_stream",
                    "metadata": {"langgraph_node": "generate_from_kb"},
                    "data": {"chunk": {"content": c}},
                }

            # Graph output metadata
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {
                    "output": {
                        "source_used": test_source,
                        "trace": test_trace,
                        "citations": test_citations,
                        "current_query": test_rewritten,
                    }
                },
            }

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "answer": test_answer,
            "source_used": test_source,
            "trace": test_trace,
            "citations": test_citations,
            "current_query": test_rewritten,
        }
        mock_graph.astream_events = mock_astream_events
        register_mock_workflow(
            workflow,
            graph=mock_graph,
            answer=test_answer,
            source_used=test_source,
            trace=test_trace,
            citations=test_citations,
        )

        request_payload = {
            "question": "How do I connect to VPN?",
            "workflow": workflow,
            "session_id": "sess-e2e-parity-001",
            "user_id": "user-e2e-parity-001",
            "stream_steps": True,
        }

        # ----------------------------------------------------------------------
        # A. Execute legacy synchronous POST /api/chat
        # ----------------------------------------------------------------------
        sync_response = client.post("/api/chat", json=request_payload)
        assert sync_response.status_code == 200
        assert "application/json" in sync_response.headers.get("content-type", "")
        sync_data = sync_response.json()

        # ----------------------------------------------------------------------
        # B. Execute streaming POST /api/chat/stream
        # ----------------------------------------------------------------------
        with client.stream("POST", "/api/chat/stream", json=request_payload) as stream_response:
            assert stream_response.status_code == 200
            assert "text/event-stream" in stream_response.headers.get("content-type", "")
            assert stream_response.headers.get("cache-control") == "no-cache"
            assert stream_response.headers.get("connection") == "keep-alive"
            assert stream_response.headers.get("x-accel-buffering") == "no"

            stream_lines = list(stream_response.iter_lines())

        parsed_events = parse_sse_events(stream_lines)
        event_types = [e["event"] for e in parsed_events]

        assert "step" in event_types
        assert "token" in event_types
        assert "metadata" in event_types

        # Reconstruct stream tokens into synthesized answer
        token_deltas = [
            e["data"]["delta"]
            for e in parsed_events
            if e["event"] == "token" and isinstance(e["data"], dict)
        ]
        reconstructed_answer = "".join(token_deltas)

        metadata_events = [e["data"] for e in parsed_events if e["event"] == "metadata"]
        assert len(metadata_events) == 1
        stream_meta = metadata_events[0]

        # ----------------------------------------------------------------------
        # C. Assert Absolute Contract Parity
        # ----------------------------------------------------------------------
        # 1. Synthesized answer matches concatenated stream tokens
        assert sync_data["answer"] == reconstructed_answer
        assert reconstructed_answer == test_answer

        # 2. Workflow routing matches
        assert sync_data["workflow"] == stream_meta["workflow"] == workflow

        # 3. Source used classification matches
        assert sync_data["source_used"] == stream_meta["source_used"] == test_source

        # 4. Citations list matches
        assert sync_data["citations"] == stream_meta["citations"] == test_citations

        # 5. Reasoning trace step sequence matches
        assert sync_data["trace"] == stream_meta["trace"] == test_trace

        # 6. Query rewriting matches
        assert sync_data["rewritten_query"] == stream_meta["rewritten_query"] == test_rewritten

        # 7. Trace ID matches
        assert sync_data["trace_id"] == stream_meta["trace_id"] == test_trace_id

    def test_e2e_streaming_step_suppression_parity(self, client, monkeypatch, register_mock_workflow):
        """Verify stream_steps=False suppresses step events while preserving identical tokens and metadata."""
        mock_handler = MagicMock()
        mock_handler.last_trace_id = "trace-steps-suppressed"
        monkeypatch.setattr("app.rag.workflows.runner.get_langfuse_callback", lambda **kwargs: mock_handler)
        monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: "trace-steps-suppressed")
        monkeypatch.setattr("app.rag.workflows.runner.flush_telemetry", lambda: None)

        async def mock_astream_events(initial_state, version="v2", config=None):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "route_question"}, "data": {}}
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "direct_answer"}, "data": {"chunk": {"content": "Suppressed steps answer"}}}
            yield {"event": "on_chain_end", "metadata": {"langgraph_node": "direct_answer"}, "data": {"output": {"source_used": "direct"}}}

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_astream_events
        register_mock_workflow("fix_single_llm", graph=mock_graph)

        # Request with stream_steps=False
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"question": "What is the policy?", "workflow": "fix_single_llm", "stream_steps": False},
        ) as resp:
            assert resp.status_code == 200
            events = parse_sse_events(list(resp.iter_lines()))

        event_types = [e["event"] for e in events]
        assert "step" not in event_types, "Step events must be suppressed when stream_steps=False"
        assert "token" in event_types
        assert "metadata" in event_types

        tokens = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
        assert tokens == "Suppressed steps answer"

    def test_e2e_streaming_vs_sync_validation_error_parity(self, client):
        """Verify error contract parity between POST /api/chat and POST /api/chat/stream."""
        # 1. Unknown workflow contract parity (HTTP 400)
        invalid_wf_payload = {"question": "How do I reset password?", "workflow": "nonexistent_wf"}

        sync_err = client.post("/api/chat", json=invalid_wf_payload)
        stream_err = client.post("/api/chat/stream", json=invalid_wf_payload)

        assert sync_err.status_code == 400
        assert stream_err.status_code == 400
        assert sync_err.json() == stream_err.json()
        assert "Unknown workflow: nonexistent_wf" in stream_err.json()["detail"]

        # 2. Empty question contract parity (HTTP 422)
        empty_payload = {"question": "", "workflow": "fix_single_llm"}
        sync_422 = client.post("/api/chat", json=empty_payload)
        stream_422 = client.post("/api/chat/stream", json=empty_payload)

        assert sync_422.status_code == 422
        assert stream_422.status_code == 422

        # 3. Question shorter than 2 chars (HTTP 422)
        short_payload = {"question": "x", "workflow": "fix_single_llm"}
        sync_short = client.post("/api/chat", json=short_payload)
        stream_short = client.post("/api/chat/stream", json=short_payload)

        assert sync_short.status_code == 422
        assert stream_short.status_code == 422


# ============================================================================
# 5. Node.js Browser-Runtime Verification Execution
# ============================================================================


class TestNodeVerificationSuites:
    """Executes Node.js verification suites for Ticket 5 parser and Ticket 6 UI lifecycle."""

    def test_node_ticket5_streaming_js_suite(self):
        """Execute tests/test_ticket5_streaming_js.js via Node.js."""
        test_file = BASE_DIR / "tests" / "test_ticket5_streaming_js.js"
        assert test_file.is_file(), f"Missing {test_file}"

        res = subprocess.run(
            ["node", str(test_file)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Ticket 5 Node tests failed:\n{res.stderr}\n{res.stdout}"
        assert "ALL 11 TICKET 5 STREAMING PARSER VERIFICATION CHECKS PASSED" in res.stdout

    def test_node_ticket6_streaming_ui_js_suite(self):
        """Execute tests/test_ticket6_streaming_ui_js.js via Node.js."""
        test_file = BASE_DIR / "tests" / "test_ticket6_streaming_ui_js.js"
        assert test_file.is_file(), f"Missing {test_file}"

        res = subprocess.run(
            ["node", str(test_file)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Ticket 6 Node tests failed:\n{res.stderr}\n{res.stdout}"
        assert "ALL 8 TICKET 6 STREAMING UI VERIFICATION CHECKS PASSED" in res.stdout
