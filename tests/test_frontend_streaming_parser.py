"""Verification tests for Ticket 5: Zero-Dependency Client SSE Parser.

Validates that static/js/app.js implements the lightweight client SSE parser adhering
to ADR 0004, including readEventStream, streamChat, parseSSEFrame, and executes
the Node.js mock ReadableStream verification test suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

BASE_DIR = Path(__file__).resolve().parent.parent


def test_app_js_contains_ticket5_sse_parser_components():
    """Verify static/js/app.js contains all required Ticket 5 streaming components."""
    app_js_path = BASE_DIR / "static" / "js" / "app.js"
    assert app_js_path.is_file(), "static/js/app.js must exist"

    content = app_js_path.read_text(encoding="utf-8")

    # Core generator and reader functions
    assert "async function* readEventStream" in content
    assert "async function* streamChat" in content
    assert "function parseSSEFrame" in content
    assert "function scheduleProgressiveRender" in content

    # Streaming endpoint reference
    assert "/api/chat/stream" in content

    # Wire protocol and error handling mechanisms
    assert "response.body.getReader()" in content
    assert 'new TextDecoder("utf-8")' in content
    assert "reader.releaseLock()" in content
    assert "split(/\\r?\\n/)" in content or "split(" in content
    assert "JSON.parse(" in content

    # Export assertions
    assert "readEventStream" in content
    assert "streamChat" in content
    assert "parseSSEFrame" in content
    assert "scheduleProgressiveRender" in content


def test_static_asset_serves_streaming_parser():
    """Verify that FastAPI serves static/js/app.js containing the streaming parser."""
    client = TestClient(app)
    response = client.get("/static/js/app.js")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    assert content_type in ("application/javascript", "text/javascript")
    assert "readEventStream" in response.text
    assert "/api/chat/stream" in response.text


def test_node_ticket5_streaming_js_verification_suite():
    """Execute tests/test_ticket5_streaming_js.js via Node.js to verify parser logic."""
    test_file = BASE_DIR / "tests" / "test_ticket5_streaming_js.js"
    assert test_file.is_file(), "tests/test_ticket5_streaming_js.js must exist"

    result = subprocess.run(
        ["node", str(test_file)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        check=False,
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    assert result.returncode == 0, f"Node test suite failed with code {result.returncode}:\n{result.stderr}"
    assert "ALL 11 TICKET 5 STREAMING PARSER VERIFICATION CHECKS PASSED" in result.stdout
