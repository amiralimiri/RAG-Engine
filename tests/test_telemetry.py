from app.core.config import Settings


def test_callback_returns_none_when_disabled(monkeypatch):
    """When langfuse_enabled is False, get_langfuse_callback and get_langfuse_client return None."""
    dummy_settings = Settings(
        _env_file=None,
        api_key="dummy",
        tavily_api_key="dummy",
        pinecone_api_key="dummy",
        admin_api_key="dummy",
        langfuse_enabled=False,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
    )
    monkeypatch.setattr("app.core.telemetry.get_settings", lambda: dummy_settings)
    from app.core.telemetry import get_langfuse_callback, get_langfuse_client

    assert get_langfuse_client() is None
    assert get_langfuse_callback("test_workflow") is None


def test_callback_returns_none_when_keys_missing(monkeypatch):
    """When public or secret key is missing/empty, return None."""
    dummy_settings = Settings(
        _env_file=None,
        api_key="dummy",
        tavily_api_key="dummy",
        pinecone_api_key="dummy",
        admin_api_key="dummy",
        langfuse_enabled=True,
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    monkeypatch.setattr("app.core.telemetry.get_settings", lambda: dummy_settings)
    from app.core.telemetry import get_langfuse_callback, get_langfuse_client

    assert get_langfuse_client() is None
    assert get_langfuse_callback("test_workflow") is None


def test_callback_created_with_tags_and_metadata(monkeypatch):
    """Provide valid keys, verify created CallbackHandler attributes (trace name, tags, metadata)."""
    dummy_settings = Settings(
        _env_file=None,
        api_key="dummy",
        tavily_api_key="dummy",
        pinecone_api_key="dummy",
        admin_api_key="dummy",
        app_env="staging",
        langfuse_enabled=True,
        langfuse_public_key="pk-valid-test",
        langfuse_secret_key="sk-valid-test",
        langfuse_host="https://cloud.langfuse.com",
    )
    monkeypatch.setattr("app.core.telemetry.get_settings", lambda: dummy_settings)
    from langfuse.langchain import CallbackHandler

    import app.core.telemetry as telemetry
    telemetry._langfuse_client = None

    handler = telemetry.get_langfuse_callback(
        "fix_single_llm",
        session_id="session-123",
        user_id="user-456",
        tags=["custom-tag"],
        metadata={"classifier": "openai_json"},
        trace_id="tr-test-789",
    )

    assert handler is not None
    assert isinstance(handler, CallbackHandler)
    assert handler.trace_name == "rag:fix_single_llm"
    assert "workflow:fix_single_llm" in handler.tags
    assert "env:staging" in handler.tags
    assert "custom-tag" in handler.tags
    assert handler.session_id == "session-123"
    assert handler.user_id == "user-456"
    assert handler.metadata == {"classifier": "openai_json"}
    assert handler._trace_context == {"trace_id": "trtest789"}


def test_extract_trace_id_handles_none_and_exceptions():
    """Verify extract_trace_id(None) is None and broken objects do not raise."""
    from app.core.telemetry import extract_trace_id

    assert extract_trace_id(None) is None

    class BrokenHandler:
        @property
        def last_trace_id(self):
            raise RuntimeError("Corrupted handler")

    assert extract_trace_id(BrokenHandler()) is None


def test_extract_trace_id_variants():
    """Verify extract_trace_id handles all supported attribute conventions."""
    from app.core.telemetry import extract_trace_id

    # Variant 1: last_trace_id (Langfuse v5)
    class HandlerWithLastTrace:
        last_trace_id = "trace-last-123"

    assert extract_trace_id(HandlerWithLastTrace()) == "trace-last-123"

    # Variant 2: _trace_context dict
    class HandlerWithContext:
        _trace_context = {"trace_id": "trace-ctx-456"}

    assert extract_trace_id(HandlerWithContext()) == "trace-ctx-456"

    # Variant 3: callable get_trace_id()
    class HandlerWithMethod:
        def get_trace_id(self):
            return "trace-method-789"

    assert extract_trace_id(HandlerWithMethod()) == "trace-method-789"

    # Variant 4: trace_id attribute
    class HandlerWithAttr:
        trace_id = "trace-attr-000"

    assert extract_trace_id(HandlerWithAttr()) == "trace-attr-000"

    # Variant 5: root_trace_id attribute
    class HandlerWithRoot:
        root_trace_id = "trace-root-111"

    assert extract_trace_id(HandlerWithRoot()) == "trace-root-111"


def test_flush_telemetry_safe_when_unconfigured(monkeypatch):
    """Call flush_telemetry() when client is None and ensure no exceptions."""
    import app.core.telemetry as telemetry
    from app.core.telemetry import flush_telemetry

    telemetry._langfuse_client = None
    flush_telemetry()  # Should not raise


def test_flush_telemetry_invokes_client_and_handles_exception(monkeypatch):
    """flush_telemetry() calls client.flush() and catches any exception safely."""
    import app.core.telemetry as telemetry
    from app.core.telemetry import flush_telemetry

    called = False

    class MockClient:
        def flush(self):
            nonlocal called
            called = True

    telemetry._langfuse_client = MockClient()
    flush_telemetry()
    assert called is True

    class FailingClient:
        def flush(self):
            raise ConnectionError("Network down")

    telemetry._langfuse_client = FailingClient()
    flush_telemetry()  # Should catch and log warning without raising


def test_initialization_exceptions_fail_open(monkeypatch):
    """Any exception during Langfuse() or CallbackHandler creation is caught and returns None."""
    dummy_settings = Settings(
        _env_file=None,
        api_key="dummy",
        tavily_api_key="dummy",
        pinecone_api_key="dummy",
        admin_api_key="dummy",
        langfuse_enabled=True,
        langfuse_public_key="pk-valid",
        langfuse_secret_key="sk-valid",
    )
    monkeypatch.setattr("app.core.telemetry.get_settings", lambda: dummy_settings)
    import app.core.telemetry as telemetry
    telemetry._langfuse_client = None

    def raise_err(*args, **kwargs):
        raise ValueError("SDK initialization failed")

    monkeypatch.setattr("app.core.telemetry.Langfuse", raise_err)

    assert telemetry.get_langfuse_client() is None
    assert telemetry.get_langfuse_callback("test_wf") is None


def test_callback_on_chain_start_injects_attributes(monkeypatch):
    """Verify that LangfuseCallbackHandler injects trace attributes on root chain start."""
    dummy_settings = Settings(
        _env_file=None,
        api_key="dummy",
        tavily_api_key="dummy",
        pinecone_api_key="dummy",
        admin_api_key="dummy",
        app_env="production",
        langfuse_enabled=True,
        langfuse_public_key="pk-valid",
        langfuse_secret_key="sk-valid",
    )
    monkeypatch.setattr("app.core.telemetry.get_settings", lambda: dummy_settings)
    import app.core.telemetry as telemetry
    telemetry._langfuse_client = None

    handler = telemetry.get_langfuse_callback(
        "fix_single_llm",
        session_id="sess-abc",
        user_id="user-xyz",
        tags=["prod-tag"],
        metadata={"custom_key": "val1"},
    )
    assert handler is not None

    captured = {}

    def mock_super_on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        captured["tags"] = tags
        captured["metadata"] = metadata
        return "chain_started"

    monkeypatch.setattr(telemetry.CallbackHandler, "on_chain_start", mock_super_on_chain_start)

    res = handler.on_chain_start(
        serialized={"name": "test_chain"},
        inputs={"question": "hi"},
        run_id="uuid-1",
        parent_run_id=None,
        tags=["initial-tag"],
        metadata={"existing": "data"},
    )

    assert res == "chain_started"
    assert "workflow:fix_single_llm" in captured["tags"]
    assert "env:production" in captured["tags"]
    assert "prod-tag" in captured["tags"]
    assert "initial-tag" in captured["tags"]
    assert captured["metadata"]["langfuse_trace_name"] == "rag:fix_single_llm"
    assert captured["metadata"]["langfuse_session_id"] == "sess-abc"
    assert captured["metadata"]["langfuse_user_id"] == "user-xyz"
    assert captured["metadata"]["custom_key"] == "val1"
    assert captured["metadata"]["existing"] == "data"



