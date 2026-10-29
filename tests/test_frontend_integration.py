"""Automated Frontend & Backend Integration Test Suite (Ticket 7).

Verifies end-to-end integration across:
1. Static asset delivery (vendored scripts/stylesheets and core frontend files)
2. Semantic HTML shell rendering, Anti-FOUC theme script, and script execution order
3. API contract compatibility (/api/workflows, /api/chat, /api/feedback)
"""

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


# ============================================================================
# 1. Static Asset Delivery Integration Tests
# ============================================================================


@pytest.mark.parametrize(
    "asset_path,expected_mimes,expected_content_token",
    [
        (
            "/static/vendor/marked.min.js",
            ("application/javascript", "text/javascript"),
            "marked",
        ),
        (
            "/static/vendor/purify.min.js",
            ("application/javascript", "text/javascript"),
            "DOMPurify",
        ),
        (
            "/static/vendor/highlight.min.js",
            ("application/javascript", "text/javascript"),
            "hljs",
        ),
        (
            "/static/vendor/highlight.theme.css",
            ("text/css",),
            ".hljs-keyword",
        ),
        (
            "/static/css/style.css",
            ("text/css",),
            ":root",
        ),
        (
            "/static/js/app.js",
            ("application/javascript", "text/javascript"),
            "generateUUID",
        ),
    ],
)
def test_static_asset_delivery_contracts(
    client, asset_path, expected_mimes, expected_content_token
):
    """Assert all vendored dependencies and core frontend files are delivered with HTTP 200,

    valid Content-Type headers, non-empty content payloads, and expected content signatures.
    """
    response = client.get(asset_path)
    assert response.status_code == 200, f"Failed to fetch static asset: {asset_path}"

    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    assert (
        content_type in expected_mimes
    ), f"Unexpected Content-Type '{content_type}' for asset {asset_path} (expected one of {expected_mimes})"

    assert (
        len(response.content) > 0
    ), f"Asset payload is empty for {asset_path}"
    assert (
        expected_content_token in response.text
    ), f"Expected token '{expected_content_token}' missing from {asset_path}"


# ============================================================================
# 2. Semantic HTML Shell Rendering Integration Tests
# ============================================================================


def test_html_shell_delivery_and_mimetype(client):
    """Assert GET / serves HTTP 200 with text/html content-type and valid markup."""
    response = client.get("/")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/html" in content_type
    assert "<!doctype html>" in response.text.lower()


def test_html_shell_contains_antifouc_theme_script(client):
    """Assert index.html contains the inline anti-FOUC theme initialization script."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert "localStorage.getItem" in html
    assert "rag_theme" in html
    assert "data-theme" in html
    assert "(prefers-color-scheme: light)" in html


def test_html_shell_critical_dom_container_ids(client):
    """Assert that index.html contains all critical DOM containers required by Tickets 1-7."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    critical_ids = [
        # Ticket 7 specified containers
        "chatMessages",
        "thinkingBubble",
        "recentChatsList",
        "workflowSelect",
        "toastContainer",
        # Acceptance criteria containers
        "chat",
        "chatForm",
        "themeToggle",
        "newChatBtn",
        "sessionsList",
        # Supporting core workspace elements
        "question",
        "sendBtn",
        "welcomeCard",
        "sidebar",
        "sidebarToggleBtn",
        "clearHistoryBtn",
        "uploadModal",
    ]

    for elem_id in critical_ids:
        expected_attr = f'id="{elem_id}"'
        assert expected_attr in html, f"Missing critical DOM container element: {expected_attr}"

    # Verify absence of obsolete legacy stub containers and prototype monkey-patch
    assert 'id="messageInput"' not in html, "Obsolete #messageInput should be replaced with #question"
    assert 'Document.prototype.getElementById' not in html, "Monkey-patch should be removed"
    assert 'id="sourceUsed"' not in html, "Obsolete #sourceUsed stub should be removed"
    assert 'id="workflowUsed"' not in html, "Obsolete #workflowUsed stub should be removed"


def test_html_shell_script_loading_order_before_body_close(client):
    """Assert exact script loading order: marked -> purify -> highlight -> app.js,

    and ensure all script declarations occur strictly before the closing </body> tag.
    """
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Locate required tags
    body_close_pos = html.lower().rfind("</body>")
    assert body_close_pos != -1, "Missing closing </body> tag in HTML response"

    marked_script = '<script src="/static/vendor/marked.min.js"></script>'
    purify_script = '<script src="/static/vendor/purify.min.js"></script>'
    highlight_script = '<script src="/static/vendor/highlight.min.js"></script>'
    app_script = '<script src="/static/js/app.js"></script>'

    pos_marked = html.find(marked_script)
    pos_purify = html.find(purify_script)
    pos_highlight = html.find(highlight_script)
    pos_app = html.find(app_script)

    assert pos_marked != -1, f"Missing script declaration: {marked_script}"
    assert pos_purify != -1, f"Missing script declaration: {purify_script}"
    assert pos_highlight != -1, f"Missing script declaration: {highlight_script}"
    assert pos_app != -1, f"Missing script declaration: {app_script}"

    # Strict loading order check
    assert pos_marked < pos_purify, "marked.min.js must precede purify.min.js"
    assert pos_purify < pos_highlight, "purify.min.js must precede highlight.min.js"
    assert pos_highlight < pos_app, "highlight.min.js must precede app.js"
    assert pos_app < body_close_pos, "app.js script tag must precede closing </body>"


# ============================================================================
# 3. API Contract Compatibility Integration Tests
# ============================================================================


def test_api_workflows_contract(client):
    """Assert GET /api/workflows returns available graph workflows matching data contracts."""
    response = client.get("/api/workflows")
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")

    data = response.json()
    assert isinstance(data, dict)
    assert "workflows" in data
    assert "default" in data

    assert isinstance(data["workflows"], list)
    assert len(data["workflows"]) >= 2
    assert "fix_single_jev" in data["workflows"]
    assert "fix_single_llm" in data["workflows"]
    assert data["default"] in data["workflows"]


def test_api_chat_contract_success(client, monkeypatch, register_mock_workflow):
    """Assert POST /api/chat contract returns trace_id, source_used, and trace arrays."""
    mock_handler = MagicMock()
    mock_handler.last_trace_id = "trace-frontend-test-999"

    monkeypatch.setattr(
        "app.api.routes.get_langfuse_callback",
        lambda **kwargs: mock_handler,
    )
    monkeypatch.setattr(
        "app.api.routes.extract_trace_id",
        lambda handler: getattr(handler, "last_trace_id", None),
    )

    register_mock_workflow(
        "fix_single_jev",
        answer="Connect to GlobalProtect VPN using your Okta MFA credentials.",
        source_used="vectorstore",
        trace=["router", "retrieve_kb", "grade_documents", "generate_answer"],
        citations=["IT Knowledge Base - VPN Handbook (Page 4)"],
    )

    payload = {
        "question": "How do I connect to VPN?",
        "workflow": "fix_single_jev",
        "session_id": "sess-integration-test-1",
        "user_id": "user-integration-test-1",
    }

    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")

    data = response.json()
    # Contract schema requirements
    assert "workflow" in data and data["workflow"] == "fix_single_jev"
    assert "answer" in data and len(data["answer"]) > 0
    assert "source_used" in data and data["source_used"] == "vectorstore"
    assert "trace" in data and isinstance(data["trace"], list)
    assert len(data["trace"]) == 4
    assert data["trace"] == ["router", "retrieve_kb", "grade_documents", "generate_answer"]
    assert "citations" in data and isinstance(data["citations"], list)
    assert "rewritten_query" in data and data["rewritten_query"] == "How do I connect to VPN?"
    assert "trace_id" in data and data["trace_id"] == "trace-frontend-test-999"


def test_api_chat_contract_error_handling(client):
    """Assert POST /api/chat enforces contract validations (unknown workflow & empty queries)."""
    # Unknown workflow
    invalid_workflow_res = client.post(
        "/api/chat",
        json={"question": "Valid question string", "workflow": "nonexistent_workflow"},
    )
    assert invalid_workflow_res.status_code == 400
    assert "Unknown workflow" in invalid_workflow_res.json().get("detail", "")

    # Query too short (< 2 characters)
    short_query_res = client.post(
        "/api/chat",
        json={"question": "?", "workflow": "fix_single_jev"},
    )
    assert short_query_res.status_code == 422


def test_api_feedback_contract_success(client, monkeypatch):
    """Assert POST /api/feedback accepts scores and comments, returning accepted status."""
    submitted_feedback = []

    def mock_submit_feedback(*args, **kwargs):
        submitted_feedback.append((args, kwargs))

    monkeypatch.setattr("app.api.routes._submit_feedback_task", mock_submit_feedback)

    payload = {
        "trace_id": "trace-frontend-test-999",
        "score": 1.0,
        "name": "user_feedback",
        "comment": "Accurate VPN guidance with clear steps.",
    }

    response = client.post("/api/feedback", json=payload)
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")

    data = response.json()
    assert data == {"status": "accepted", "trace_id": "trace-frontend-test-999"}

    # Assert background task was enqueued with identical payload attributes
    assert len(submitted_feedback) == 1
    _, task_kwargs = submitted_feedback[0]
    assert task_kwargs["trace_id"] == "trace-frontend-test-999"
    assert task_kwargs["score"] == 1.0
    assert task_kwargs["name"] == "user_feedback"
    assert task_kwargs["comment"] == "Accurate VPN guidance with clear steps."


def test_api_feedback_contract_validation(client):
    """Assert POST /api/feedback rejects invalid scores or missing trace_id."""
    # Out of range scores
    res_high = client.post("/api/feedback", json={"trace_id": "tr-1", "score": 1.5})
    assert res_high.status_code == 422

    res_low = client.post("/api/feedback", json={"trace_id": "tr-1", "score": -0.1})
    assert res_low.status_code == 422

    # Missing trace_id
    res_missing_trace = client.post("/api/feedback", json={"score": 1.0})
    assert res_missing_trace.status_code == 422
