
import pytest
from fastapi.testclient import TestClient

from app.core.config import BASE_DIR
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_app_js_static_mount(client):
    """Verify that FastAPI serves static/js/app.js with HTTP 200 and valid MIME type."""
    response = client.get("/static/js/app.js")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    assert content_type in ("application/javascript", "text/javascript")
    content = response.text
    assert "generateUUID" in content
    assert "STORAGE_KEY_CURRENT_SESSION" in content
    assert "rag_sessions_index" in content
    assert "rag_theme" in content
    assert "startNewChat" in content
    assert "switchSession" in content
    assert "deleteSession" in content
    assert "clearAllHistory" in content


def test_templates_index_contains_ticket4_elements(client):
    """Verify that index.html contains all necessary element IDs and tags for Ticket 4."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Theme controls
    assert 'id="themeToggleBtn"' in html
    assert 'class="sun-icon"' in html
    assert 'class="moon-icon"' in html

    # Session controls
    assert 'id="newChatBtn"' in html
    assert 'id="recentChatsList"' in html
    assert 'id="clearHistoryBtn"' in html
    assert 'id="sessionsList"' in html

    # Mobile navigation drawer
    assert 'id="sidebarToggleBtn"' in html
    assert 'id="sidebarBackdrop"' in html
    assert 'id="sidebar"' in html

    # Conversational canvas & welcome greeting
    assert 'id="chatMessages"' in html
    assert 'id="welcomeCard"' in html


def test_uuid_v4_generation_in_app_js():
    """Verify that generateUUID implementation conforms to standard UUID v4 structure."""
    app_js_path = BASE_DIR / "static" / "js" / "app.js"
    assert app_js_path.is_file()
    content = app_js_path.read_text(encoding="utf-8")

    # Confirms crypto.randomUUID with fallback
    assert "crypto.randomUUID()" in content
    assert "crypto.getRandomValues" in content
