import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import BASE_DIR


@pytest.fixture
def client():
    return TestClient(app)


def test_index_template_thinking_and_vendor_order(client):
    """Verify that templates/index.html includes all Ticket 5 DOM elements and proper script ordering."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # In-feed thinking bubble components
    assert 'id="thinkingBubble"' in html
    assert 'class="radar-pulse"' in html
    assert 'id="thinkingWorkflow"' in html
    assert 'class="thinking-workflow"' in html
    assert 'id="thinkingTimer"' in html
    assert 'class="thinking-timer"' in html

    # Vendor script inclusions and ordering
    marked_idx = html.find("/static/vendor/marked.min.js")
    purify_idx = html.find("/static/vendor/purify.min.js")
    hljs_idx = html.find("/static/vendor/highlight.min.js")
    app_idx = html.find("/static/js/app.js")

    assert marked_idx != -1, "marked.min.js must be included in index.html"
    assert purify_idx != -1, "purify.min.js must be included in index.html"
    assert hljs_idx != -1, "highlight.min.js must be included in index.html"
    assert app_idx != -1, "app.js must be included in index.html"

    # Must be loaded in order: marked -> purify -> highlight -> app.js
    assert marked_idx < purify_idx < hljs_idx < app_idx, (
        "Scripts must be loaded in strict order: marked -> purify -> hljs -> app.js"
    )


def test_app_js_markdown_and_sanitization_pipeline():
    """Verify that static/js/app.js contains the full Markdown and DOMPurify sanitization pipeline."""
    app_js_path = BASE_DIR / "static" / "js" / "app.js"
    assert app_js_path.is_file()
    content = app_js_path.read_text(encoding="utf-8")

    # Pipeline functions
    assert "function renderMarkdown" in content
    assert "function initMarked" in content
    assert "function copyToClipboard" in content
    assert "function handleCopyCode" in content

    # Marked options
    assert "breaks: true" in content
    assert "gfm: true" in content

    # Code block wrapper markup
    assert "code-block-wrapper" in content
    assert "code-block-header" in content
    assert "code-lang" in content
    assert "copy-code-btn" in content
    assert "copy-label" in content

    # Syntax highlighting
    assert "hljs" in content
    assert "highlight" in content

    # DOMPurify configuration
    assert "DOMPurify" in content
    assert "USE_PROFILES" in content
    assert "FORBID_TAGS" in content
    assert "FORBID_ATTR" in content
    assert "onerror" in content
    assert "onload" in content

    # Copy 2000ms feedback state
    assert "Copied!" in content
    assert "2000" in content
    assert "copied" in content


def test_app_js_thinking_state_indicator_and_lifecycle():
    """Verify that static/js/app.js implements the agentic thinking state and timer ticker."""
    app_js_path = BASE_DIR / "static" / "js" / "app.js"
    assert app_js_path.is_file()
    content = app_js_path.read_text(encoding="utf-8")

    # Thinking state functions
    assert "function showThinking" in content
    assert "function hideThinking" in content

    # Timer ticker interval and formatting
    assert "thinkingTimerInterval" in content
    assert "100" in content  # 100ms ticker interval
    assert "toFixed(1)" in content  # 0.0s elapsed formatting

    # Thinking element references
    assert "thinkingBubble" in content
    assert "thinkingWorkflow" in content
    assert "thinkingTimer" in content

    # Composer inputs disabling
    assert "question.disabled = true" in content
    assert "question.disabled = false" in content
    assert "btn.disabled = true" in content


def test_style_css_contains_ticket5_classes():
    """Verify that static/css/style.css defines styling for code blocks and thinking indicators."""
    css_path = BASE_DIR / "static" / "css" / "style.css"
    assert css_path.is_file()
    content = css_path.read_text(encoding="utf-8")

    # Code block styles
    assert ".code-block-wrapper" in content
    assert ".code-block-header" in content
    assert ".code-lang" in content
    assert ".copy-code-btn" in content
    assert ".copy-code-btn.copied" in content

    # Thinking state styles
    assert ".thinking-bubble" in content
    assert ".thinking-content" in content
    assert ".radar-pulse" in content
    assert "@keyframes radar-ping" in content
    assert ".thinking-workflow" in content
    assert ".thinking-timer" in content
