import re
import uuid

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langfuse.langchain import CallbackHandler

import app.core.telemetry as telemetry
from app.api.middleware import CorrelationLoggingMiddleware
from app.core.config import Settings
from app.core.telemetry import (
    LangfuseCallbackHandler,
    extract_trace_id,
    get_langfuse_callback,
)

HEX_32_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def clean_telemetry_state():
    """Ensure clean contextvars and reset global telemetry client."""
    structlog.contextvars.clear_contextvars()
    telemetry._langfuse_client = None
    yield
    structlog.contextvars.clear_contextvars()
    telemetry._langfuse_client = None


@pytest.fixture
def mock_langfuse_settings(monkeypatch):
    """Provide valid Langfuse settings to enable handler instantiation."""
    settings = Settings(
        _env_file=None,
        api_key="dummy",
        tavily_api_key="dummy",
        pinecone_api_key="dummy",
        admin_api_key="dummy",
        langfuse_enabled=True,
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://localhost:1234",
    )
    monkeypatch.setattr("app.core.telemetry.get_settings", lambda: settings)
    return settings


def test_generated_trace_id_matches_hex32_and_accepted_by_langfuse(mock_langfuse_settings):
    """Generated trace ID must match ^[0-9a-f]{32}$ and be accepted by Langfuse CallbackHandler without ValueError."""
    handler = get_langfuse_callback("fix_single_llm")
    assert handler is not None
    assert isinstance(handler, LangfuseCallbackHandler)

    extracted_tid = extract_trace_id(handler)
    assert extracted_tid is not None
    assert HEX_32_PATTERN.match(extracted_tid), f"Generated trace_id '{extracted_tid}' does not match ^[0-9a-f]{{32}}$"

    # Invoking on_chain_start must succeed without ValueError: invalid literal for int() with base 16
    handler.on_chain_start(
        serialized={"name": "test_chain"},
        inputs={"question": "Test query"},
        run_id=uuid.uuid4(),
    )


def test_hyphenated_uuid_fails_in_raw_langfuse_but_sanitized_passes(mock_langfuse_settings):
    """Demonstrate that raw hyphenated UUID fails in Langfuse client._create_remote_parent_span, but sanitized 32-hex string succeeds."""
    client = telemetry.get_langfuse_client()
    assert client is not None

    raw_hyphenated_uuid = str(uuid.uuid4())
    assert "-" in raw_hyphenated_uuid

    # Raw hyphenated trace ID raises ValueError inside _create_remote_parent_span
    with pytest.raises(ValueError, match="invalid literal for int\\(\\) with base 16"):
        client._create_remote_parent_span(
            trace_id=raw_hyphenated_uuid,
            parent_span_id=None,
        )

    # When sanitized via .replace('-', '').lower(), it succeeds without ValueError
    sanitized_id = raw_hyphenated_uuid.replace("-", "").lower()
    assert HEX_32_PATTERN.match(sanitized_id)

    remote_span = client._create_remote_parent_span(
        trace_id=sanitized_id,
        parent_span_id=None,
    )
    assert remote_span is not None

    # CallbackHandler with sanitized trace_id starts observation cleanly without errors
    sanitized_handler = CallbackHandler(
        public_key="pk-lf-test",
        trace_context={"trace_id": sanitized_id},
    )
    sanitized_handler.on_chain_start(
        serialized={"name": "test_chain"},
        inputs={"question": "Test query"},
        run_id=uuid.uuid4(),
    )


def test_trace_id_sanitized_from_existing_context(mock_langfuse_settings):
    """When trace_id exists in structlog contextvars with hyphens, it is sanitized to 32 lowercase hex characters."""
    hyphenated_id = "2a59f821-8717-4700-ba27-b29ddefd815d"
    structlog.contextvars.bind_contextvars(trace_id=hyphenated_id)

    handler = get_langfuse_callback("fix_single_jev")
    assert handler is not None

    extracted_tid = extract_trace_id(handler)
    assert extracted_tid == "2a59f82187174700ba27b29ddefd815d"
    assert HEX_32_PATTERN.match(extracted_tid)

    handler.on_chain_start(
        serialized={"name": "test_chain"},
        inputs={"question": "Test query"},
        run_id=uuid.uuid4(),
    )


def test_trace_id_sanitized_from_explicit_argument(mock_langfuse_settings):
    """When trace_id is passed with uppercase and hyphens, it is sanitized to 32 lowercase hex characters."""
    raw_id = "2A59F821-8717-4700-BA27-B29DDEFD815D"
    handler = get_langfuse_callback("fix_single_llm", trace_id=raw_id)
    assert handler is not None

    extracted_tid = extract_trace_id(handler)
    assert extracted_tid == "2a59f82187174700ba27b29ddefd815d"
    assert HEX_32_PATTERN.match(extracted_tid)

    handler.on_chain_start(
        serialized={"name": "test_chain"},
        inputs={"question": "Test query"},
        run_id=uuid.uuid4(),
    )


def test_middleware_sanitizes_x_trace_id_header():
    """CorrelationLoggingMiddleware sanitizes x-trace-id header into contextvars."""
    app = FastAPI()
    app.add_middleware(CorrelationLoggingMiddleware)

    captured_trace_id = None

    @app.get("/test-trace")
    def route_endpoint():
        nonlocal captured_trace_id
        captured_trace_id = structlog.contextvars.get_contextvars().get("trace_id")
        return {"ok": True}

    client = TestClient(app)
    client.get("/test-trace", headers={"x-trace-id": "2A59F821-8717-4700-BA27-B29DDEFD815D"})

    assert captured_trace_id == "2a59f82187174700ba27b29ddefd815d"
    assert HEX_32_PATTERN.match(captured_trace_id)
