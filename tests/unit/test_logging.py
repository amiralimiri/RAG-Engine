import inspect
import io
import json
import logging
from unittest.mock import patch

import pytest
import structlog
from fastapi.testclient import TestClient

from app.api.routes import capture_context
from app.core.config import Settings
from app.core.logger import (
    configure_logging,
    scrub_sensitive_data,
)
from app.main import app
from app.rag.engine import direct_answer


@pytest.fixture(autouse=True)
def clean_context():
    """Ensure contextvars are cleared before and after each test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def test_scrub_sensitive_keys():
    """Verify recursive key-based redaction."""
    payload = {
        "event": "user_login",
        "password": "SuperSecretPassword123!",
        "api_key": "some-secret-key",
        "auth_token": "token-value-xyz",
        "authorization": "Basic dXNlcjpwYXNz",
        "cookie": "session_id=abc",
        "nested": {
            "secret_field": "do_not_leak",
            "normal_field": "visible_value",
            "tokens": ["sensitive_token_1", "sensitive_token_2"],
            "items": ["normal", {"admin_token": "classified"}],
        },
        "safe_key": "public_data",
    }
    scrubbed = scrub_sensitive_data(None, "info", payload)

    assert scrubbed["password"] == "[REDACTED]"
    assert scrubbed["api_key"] == "[REDACTED]"
    assert scrubbed["auth_token"] == "[REDACTED]"
    assert scrubbed["authorization"] == "[REDACTED]"
    assert scrubbed["cookie"] == "[REDACTED]"
    assert scrubbed["safe_key"] == "public_data"
    assert scrubbed["nested"]["secret_field"] == "[REDACTED]"
    assert scrubbed["nested"]["normal_field"] == "visible_value"
    assert scrubbed["nested"]["tokens"] == "[REDACTED]"
    assert scrubbed["nested"]["items"][1]["admin_token"] == "[REDACTED]"


def test_scrub_regex_patterns():
    """Verify regex pattern redaction for tokens in strings."""
    payload = {
        "event": "API call with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN and key sk-12345678901234567890123456",
        "extra_info": "Connected via tvly-abcdefghijklmnopqrstuvwxyz123456 and pcsk_12345678901234567890123456789012",
    }
    scrubbed = scrub_sensitive_data(None, "info", payload)

    assert "Bearer [REDACTED]" in scrubbed["event"]
    assert "sk-1234567890" not in scrubbed["event"]
    assert "[REDACTED]" in scrubbed["event"]
    assert "tvly-" not in scrubbed["extra_info"]
    assert "pcsk_" not in scrubbed["extra_info"]


def test_stdlib_bridge_with_contextvars():
    """Verify standard library logger passes through structlog pipeline with contextvars."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)

    # Use a mock settings with json format
    with patch("app.core.logger.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            log_format="json",
            log_level="INFO",
            log_to_file=False,
            app_env="production",
        )
        configure_logging()

    # Re-attach custom stream handler with the configured formatter to capture output
    root = logging.getLogger()
    original_streams = [getattr(h, "stream", None) for h in root.handlers]
    for h in root.handlers:
        h.setStream(log_capture)

    try:
        structlog.contextvars.bind_contextvars(
            request_id="req-unit-123",
            trace_id="trace-unit-456",
            step="test_step",
        )

        stdlib_logger = logging.getLogger("test.stdlib.logger")
        stdlib_logger.info("Test stdlib log event", extra={"user_id": "u-42"})

        output = log_capture.getvalue().strip()
        assert output, "Expected log output, got empty string"
        log_data = json.loads(output)

        assert log_data["event"] == "Test stdlib log event"
        assert log_data["logger"] == "test.stdlib.logger"
        assert log_data["level"] == "info"
        assert log_data["request_id"] == "req-unit-123"
        assert log_data["trace_id"] == "trace-unit-456"
        assert log_data["step"] == "test_step"
        assert log_data["user_id"] == "u-42"
        assert "timestamp" in log_data
    finally:
        for h, orig in zip(root.handlers, original_streams):
            if orig is not None and hasattr(h, "setStream"):
                h.setStream(orig)


def test_noise_suppression():
    """Verify external library loggers are clamped to WARNING."""
    configure_logging()
    noisy_loggers = [
        "httpx",
        "httpcore",
        "openai",
        "langchain",
        "watchfiles",
        "uvicorn.access",
    ]
    for name in noisy_loggers:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_correlation_middleware_request_id():
    """Verify middleware assigns or preserves X-Request-ID header."""
    with TestClient(app) as client:
        # Without header: auto-generate UUID4
        res1 = client.get("/api/health")
        assert res1.status_code == 200
        assert "X-Request-ID" in res1.headers
        generated_id = res1.headers["X-Request-ID"]
        assert len(generated_id) > 10

        # With header: echo back client-supplied ID
        custom_id = "custom-trace-uuid-777"
        res2 = client.get("/api/health", headers={"X-Request-ID": custom_id})
        assert res2.status_code == 200
        assert res2.headers["X-Request-ID"] == custom_id


def test_healthcheck_access_log_level(caplog):
    """Verify /api/health emits at DEBUG level and not INFO."""
    configure_logging()
    with TestClient(app) as client:
        with caplog.at_level(logging.DEBUG):
            res = client.get("/api/health")
            assert res.status_code == 200

        # Check records emitted to app.access
        access_records = [r for r in caplog.records if r.name == "app.access"]
        for record in access_records:
            if hasattr(record, "http_path") and record.http_path == "/api/health":
                assert record.levelno == logging.DEBUG


def test_background_task_context_preservation():
    """Verify background tasks preserve context snapshot via capture_context."""
    captured = []

    def sample_task(val: str):
        captured.append({
            "val": val,
            "context": structlog.contextvars.get_contextvars(),
        })

    structlog.contextvars.bind_contextvars(
        request_id="req-bg-999",
        trace_id="trace-bg-888",
    )

    bound_runner = capture_context(sample_task)

    # Clear context in main thread to simulate HTTP response teardown
    structlog.contextvars.clear_contextvars()
    assert structlog.contextvars.get_contextvars() == {}

    # Run background task
    bound_runner("test_value")

    assert len(captured) == 1
    assert captured[0]["val"] == "test_value"
    assert captured[0]["context"]["request_id"] == "req-bg-999"
    assert captured[0]["context"]["trace_id"] == "trace-bg-888"


@pytest.mark.asyncio
async def test_langgraph_step_contextvars(monkeypatch):
    """Verify workflow nodes bind step to contextvars."""
    state = {
        "question": "test question",
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
    }

    from unittest.mock import AsyncMock, MagicMock
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
    mock_llm.invoke = MagicMock(return_value=MagicMock(content="answer"))

    from app.rag import engine
    monkeypatch.setattr(engine, "get_chat_model", lambda: mock_llm)

    structlog.contextvars.clear_contextvars()
    res = direct_answer(state, model=mock_llm)
    if inspect.iscoroutine(res):
        await res
    assert structlog.contextvars.get_contextvars().get("step") == "direct_answer"


def test_safe_key_exemptions_not_redacted():
    """Verify correlation keys, token counters, and author metadata are not over-redacted."""
    payload = {
        "event": "llm_completion",
        "session_id": "sess-test-999",
        "prompt_tokens": 120,
        "completion_tokens": 45,
        "total_tokens": 165,
        "author": "Alice Engineer",
        "authors": ["Bob", "Charlie"],
        "nested": {
            "session_id": "nested-sess-111",
            "total_tokens": 500,
            "author": "Documentation Team",
        },
        # Ensure actual credentials in the same payload are still redacted
        "password": "SecretPassword",
        "api_key": "sk-12345678901234567890123456",
    }
    scrubbed = scrub_sensitive_data(None, "info", payload)

    assert scrubbed["session_id"] == "sess-test-999"
    assert scrubbed["prompt_tokens"] == 120
    assert scrubbed["completion_tokens"] == 45
    assert scrubbed["total_tokens"] == 165
    assert scrubbed["author"] == "Alice Engineer"
    assert scrubbed["authors"] == ["Bob", "Charlie"]
    assert scrubbed["nested"]["session_id"] == "nested-sess-111"
    assert scrubbed["nested"]["total_tokens"] == 500
    assert scrubbed["nested"]["author"] == "Documentation Team"
    assert scrubbed["password"] == "[REDACTED]"
    assert scrubbed["api_key"] == "[REDACTED]"


def test_namedtuple_logging_safety():
    """Verify logging a namedtuple raises zero TypeError exceptions and preserves attributes."""
    import urllib.parse
    from collections import namedtuple

    url_result = urllib.parse.urlsplit("https://example.com/docs/api?q=123")
    Point = namedtuple("Point", ["x", "y"])
    pt = Point(10, 20)

    payload = {
        "event": "parse_complete",
        "parsed_url": url_result,
        "coordinates": pt,
    }

    # Must execute cleanly without raising TypeError: ... missing required positional arguments
    scrubbed = scrub_sensitive_data(None, "info", payload)
    assert scrubbed["parsed_url"].scheme == "https"
    assert scrubbed["parsed_url"].netloc == "example.com"
    assert scrubbed["coordinates"].x == 10
    assert scrubbed["coordinates"].y == 20


def test_recursion_depth_and_circular_safety():
    """Verify circular or deeply nested structures degrade gracefully at max_depth without RecursionError."""
    # 1. Circular dictionary
    circular_dict = {"name": "root"}
    circular_dict["self"] = circular_dict

    # 2. Circular list
    circular_list = [1, 2]
    circular_list.append(circular_list)

    # 3. Deeply nested dictionary (> 15 levels)
    deep_dict = {"depth": 0}
    curr = deep_dict
    for i in range(1, 20):
        curr["child"] = {"depth": i}
        curr = curr["child"]

    payload = {
        "event": "complex_structure",
        "circular_d": circular_dict,
        "circular_l": circular_list,
        "deep": deep_dict,
    }

    # Must not raise RecursionError
    scrubbed = scrub_sensitive_data(None, "info", payload)
    assert scrubbed["event"] == "complex_structure"
    assert scrubbed["circular_d"]["self"] == "[CIRCULAR]"
    assert scrubbed["circular_l"][2] == "[CIRCULAR]"


def test_rotating_file_handler_plain_text_no_ansi(tmp_path, monkeypatch):
    """Assert RotatingFileHandler writes plain text without ANSI escape sequences."""
    import re
    import sys
    log_file = tmp_path / "app.log"
    with patch("app.core.logger.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            log_format="console",
            log_level="INFO",
            log_to_file=True,
            log_file_path=str(log_file),
            app_env="development",
        )
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        configure_logging()

    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    file_handler = file_handlers[0]

    stdlib_logger = logging.getLogger("test.ansi.check")
    stdlib_logger.info("Test clean log message", extra={"key": "value"})

    struct_logger = structlog.get_logger("test.ansi.struct")
    struct_logger.info("Test structlog clean log", code=200)

    file_handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "Test clean log message" in content
    assert "Test structlog clean log" in content
    ansi_regex = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
    assert not ansi_regex.search(content), f"Found raw ANSI codes in log file: {content}"


def test_noise_suppression_includes_new_loggers():
    """Assert httpx2, langchain_openai, and langchain_core log levels are set to WARNING."""
    configure_logging()
    for name in ["httpx2", "langchain_openai", "langchain_core"]:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_trace_id_present_in_workflow_execution_logs(monkeypatch, capsys, register_mock_workflow):
    """Assert trace_id is present in log events emitted during workflow execution."""
    from unittest.mock import MagicMock

    dummy_settings = Settings(
        langfuse_enabled=True,
        langfuse_public_key="pk-dummy",
        langfuse_secret_key="sk-dummy",
        log_format="json",
        log_to_file=False,
    )
    monkeypatch.setattr("app.core.telemetry.get_settings", lambda: dummy_settings)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: dummy_settings)
    monkeypatch.setattr("app.core.logger.get_settings", lambda: dummy_settings)

    mock_client = MagicMock()
    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: mock_client)

    captured_workflow_logs = []

    mock_graph = MagicMock()

    def _invoke(state, config=None):
        node_logger = logging.getLogger("app.rag.route_question")
        node_logger.info("Routing question to KB")
        captured_workflow_logs.append(dict(structlog.contextvars.get_contextvars()))
        return {
            "answer": "Answer from KB",
            "source_used": "kb",
            "trace": ["route_question"],
            "citations": [],
            "current_query": state.get("question"),
        }

    mock_graph.invoke = _invoke

    register_mock_workflow(
        "fix_single_llm",
        graph=mock_graph,
        answer="Answer from KB",
        source_used="kb",
        trace=["route_question"],
    )

    with TestClient(app) as client:
        res = client.post("/api/chat", json={"question": "How to reset VPN?", "workflow": "fix_single_llm"})
        assert res.status_code == 200
        res_data = res.json()
        assert res_data.get("trace_id") is not None
        response_trace_id = res_data["trace_id"]

    assert len(captured_workflow_logs) == 1
    assert captured_workflow_logs[0].get("trace_id") == response_trace_id

    captured = capsys.readouterr().out
    output_lines = []
    for line in captured.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            output_lines.append(json.loads(line))
        except Exception:
            pass

    routing_logs = [l for l in output_lines if l.get("event") == "Routing question to KB"]
    assert len(routing_logs) >= 1
    assert routing_logs[0].get("trace_id") == response_trace_id


