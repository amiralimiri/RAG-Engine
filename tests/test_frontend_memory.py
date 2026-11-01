import subprocess

from app.core.config import BASE_DIR


def test_app_js_contains_ticket7_memory_and_branching_symbols() -> None:
    """Verify static/js/app.js contains all Ticket 7 symbols, classes, and handlers."""
    css_path = BASE_DIR / "static" / "css" / "style.css"
    assert css_path.is_file()

    js_path = BASE_DIR / "static" / "js" / "app.js"
    assert js_path.is_file()
    content = js_path.read_text(encoding="utf-8")

    # Resumption & session endpoints
    assert "/api/chat/sessions" in content
    assert "/api/chat/" in content
    assert "/fork" in content

    # Edit turn & action banner UI
    assert "btn-edit-turn" in content
    assert "turnActionBanner" in content
    assert "turn-action-banner" in content
    assert "btnRewindSubmit" in content
    assert "btnForkSubmit" in content
    assert "btnCancelEdit" in content
    assert "rewind_turn_index" in content


def test_style_css_contains_ticket7_rules() -> None:
    """Verify static/css/style.css defines required CSS rules for turn edit button and action banner."""
    css_path = BASE_DIR / "static" / "css" / "style.css"
    assert css_path.is_file()
    content = css_path.read_text(encoding="utf-8")

    assert ".btn-edit-turn" in content
    assert ".turn-action-banner" in content
    assert ".banner-title" in content


def test_node_ticket7_memory_ui_suite() -> None:
    """Execute Node.js test_ticket7_memory_ui_js.js to verify browser mock DOM lifecycle."""
    test_file = BASE_DIR / "tests" / "test_ticket7_memory_ui_js.js"
    assert test_file.is_file()

    result = subprocess.run(
        ["node", str(test_file)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Node verification failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "ALL 7 TICKET 7 VERIFICATION CHECKS PASSED" in result.stdout
