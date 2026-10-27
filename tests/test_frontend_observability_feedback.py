import subprocess
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import BASE_DIR


@pytest.fixture
def client():
    return TestClient(app)


def test_templates_index_contains_ticket6_toast_container(client):
    """Verify that templates/index.html includes #toastContainer with accessibility attributes."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'id="toastContainer"' in html
    assert 'class="toast-container"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-atomic="true"' in html


def test_app_js_contains_ticket6_observability_and_feedback():
    """Verify that static/js/app.js contains all Ticket 6 observability and feedback logic."""
    app_js_path = BASE_DIR / "static" / "js" / "app.js"
    assert app_js_path.is_file()
    content = app_js_path.read_text(encoding="utf-8")

    # Toast functions & classes
    assert "function showToast" in content
    assert "toastContainer" in content
    assert "toast-success" in content
    assert "toast-error" in content
    assert "toast-info" in content
    assert "toast-fade-out" in content

    # Reasoning Accordion elements
    assert "reasoning-accordion" in content
    assert "reasoning-summary" in content
    assert "reasoning-icon" in content
    assert "source-badge" in content
    assert "badge-vectorstore" in content
    assert "badge-web_search" in content
    assert "workflow-chip" in content
    assert "trace-id-chip" in content
    assert "data-trace-id" in content
    assert "step-item" in content
    assert "step-dot" in content

    # Interactive Trace ID copying
    assert "function handleCopyTraceId" in content
    assert "Trace ID copied for Langfuse debugging" in content

    # Feedback Bar & Actions
    assert "feedback-bar" in content
    assert "feedback-btn" in content
    assert "thumbs-up" in content
    assert "thumbs-down" in content
    assert "add-note-btn" in content
    assert "feedback-status-msg" in content
    assert 'data-score="1.0"' in content
    assert 'data-score="0.0"' in content
    assert "function submitFeedback" in content
    assert "function updateMessageFeedback" in content
    assert "/api/feedback" in content

    # Comment micro-popover
    assert "function toggleCommentPopover" in content
    assert "function closeAllCommentPopovers" in content
    assert "comment-popover" in content
    assert "popover-submit" in content
    assert "popover-cancel" in content


def test_style_css_contains_ticket6_rules():
    """Verify static/css/style.css defines required CSS rules for accordions, badges, feedback, and toasts."""
    css_path = BASE_DIR / "static" / "css" / "style.css"
    assert css_path.is_file()
    content = css_path.read_text(encoding="utf-8")

    # Accordion
    assert "details.reasoning-accordion" in content
    assert "summary.reasoning-summary" in content
    assert ".accordion-arrow" in content
    assert ".reasoning-content" in content
    assert ".step-item" in content
    assert ".step-dot" in content

    # Badges & Chips
    assert ".badge-vectorstore" in content
    assert ".badge-web_search" in content
    assert ".workflow-chip" in content
    assert ".trace-id-chip" in content

    # Feedback bar
    assert ".feedback-bar" in content
    assert ".feedback-btn" in content
    assert ".feedback-btn.selected" in content
    assert ".feedback-status-msg" in content
    assert ".comment-popover" in content
    assert ".popover-submit" in content
    assert ".popover-cancel" in content

    # Toasts
    assert "#toastContainer" in content
    assert ".toast" in content
    assert ".toast.toast-success" in content
    assert ".toast.toast-error" in content
    assert ".toast.toast-info" in content
    assert "@keyframes toast-slide-in" in content
    assert "@keyframes toast-slide-out" in content


def test_feedback_api_contract_with_frontend(client, monkeypatch):
    """Verify that POST /api/feedback adheres to frontend contract for both thumbs vote and comment."""
    submitted_tasks = []

    def mock_submit_task(*args, **kwargs):
        submitted_tasks.append((args, kwargs))

    monkeypatch.setattr("app.api.routes._submit_feedback_task", mock_submit_task)

    # 1. Thumbs Up vote without comment
    res1 = client.post(
        "/api/feedback",
        json={"trace_id": "tr-456", "score": 1.0, "name": "user_feedback", "comment": None},
    )
    assert res1.status_code == 200
    assert res1.json() == {"status": "accepted", "trace_id": "tr-456"}

    # 2. Thumbs Down vote with comment note
    res2 = client.post(
        "/api/feedback",
        json={
            "trace_id": "tr-456",
            "score": 0.0,
            "name": "user_feedback",
            "comment": "VPN configuration steps were outdated.",
        },
    )
    assert res2.status_code == 200
    assert res2.json() == {"status": "accepted", "trace_id": "tr-456"}

    assert len(submitted_tasks) == 2
    assert submitted_tasks[1][1]["comment"] == "VPN configuration steps were outdated."


def test_node_ticket6_js_verification_suite():
    """Execute Node.js test_ticket6_js.js to verify browser mock DOM lifecycle."""
    test_file = BASE_DIR / "tests" / "test_ticket6_js.js"
    assert test_file.is_file()

    result = subprocess.run(
        ["node", str(test_file)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Node verification failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "ALL 8 TICKET 6 VERIFICATION CHECKS PASSED" in result.stdout
