import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import BASE_DIR


@pytest.fixture
def client():
    return TestClient(app)


def test_vendor_directory_and_files_exist():
    """Verify that static/vendor/ exists and contains all required vendor assets."""
    vendor_dir = BASE_DIR / "static" / "vendor"
    assert vendor_dir.is_dir(), "static/vendor directory should exist"

    expected_files = [
        "marked.min.js",
        "purify.min.js",
        "highlight.min.js",
        "highlight.theme.css",
    ]

    for filename in expected_files:
        file_path = vendor_dir / filename
        assert file_path.is_file(), f"{filename} should exist in static/vendor/"
        assert file_path.stat().st_size > 0, f"{filename} should not be empty"


def test_fastapi_serves_vendor_assets(client):
    """Verify that FastAPI static mount serves all 4 vendor assets with HTTP 200 and valid MIME types."""
    vendor_assets = [
        ("/static/vendor/marked.min.js", ("application/javascript", "text/javascript")),
        ("/static/vendor/purify.min.js", ("application/javascript", "text/javascript")),
        ("/static/vendor/highlight.min.js", ("application/javascript", "text/javascript")),
        ("/static/vendor/highlight.theme.css", ("text/css",)),
    ]

    for path, expected_mimes in vendor_assets:
        response = client.get(path)
        assert response.status_code == 200, f"Failed to retrieve {path}"
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        assert content_type in expected_mimes, f"Unexpected MIME type {content_type} for {path}"
        assert len(response.content) > 0, f"Empty response content for {path}"


def test_vendor_asset_contents():
    """Verify integrity of vendored script and stylesheet contents."""
    vendor_dir = BASE_DIR / "static" / "vendor"

    marked_content = (vendor_dir / "marked.min.js").read_text(encoding="utf-8")
    assert "marked" in marked_content
    assert "window.marked" in marked_content

    purify_content = (vendor_dir / "purify.min.js").read_text(encoding="utf-8")
    assert "DOMPurify" in purify_content
    assert "window.DOMPurify" in purify_content

    hljs_content = (vendor_dir / "highlight.min.js").read_text(encoding="utf-8")
    assert "hljs" in hljs_content
    assert "window.hljs" in hljs_content
    assert "powershell" in hljs_content

    theme_content = (vendor_dir / "highlight.theme.css").read_text(encoding="utf-8")
    expected_tokens = [
        ".hljs-keyword",
        ".hljs-string",
        ".hljs-comment",
        ".hljs-function",
        ".hljs-number",
        ".hljs-built_in",
        "--hljs-",
        "var(--code-bg",
    ]
    for token in expected_tokens:
        assert token in theme_content, f"Token {token} not found in highlight.theme.css"
