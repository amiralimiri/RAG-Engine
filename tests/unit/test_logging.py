import io
import json
import logging
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
import structlog
from app.api.middleware import SUPPRESSED_PATHS, CorrelationLoggingMiddleware
from app.api.routes import capture_context, run_with_context
from app.core.config import Settings
from app.core.logger import (
    configure_logging,
    get_logger,
    scrub_sensitive_data,
)
from app.main import app
from app.rag.fix_single_jev import direct_answer as jev_direct_answer
from app.rag.fix_single_llm import direct_answer as llm_direct_answer


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
    for h in root.handlers:
        h.setStream(log_capture)

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


def test_langgraph_step_contextvars():
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

    structlog.contextvars.clear_contextvars()
    llm_direct_answer(state)
    assert structlog.contextvars.get_contextvars().get("step") == "direct_answer"

    structlog.contextvars.clear_contextvars()
    jev_direct_answer(state)
    assert structlog.contextvars.get_contextvars().get("step") == "direct_answer"
