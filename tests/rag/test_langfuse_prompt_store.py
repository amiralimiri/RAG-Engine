from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import RunnableConfig
from langfuse.model import TextPromptClient

from app.core.config import Settings
from app.rag.prompts.base import CANONICAL_PROMPTS, PromptStore, RenderedPrompt
from app.rag.prompts.langfuse import LangfusePromptStore
from app.rag.prompts.local import LocalPromptStore


def _make_settings(**kwargs: Any) -> Settings:
    return Settings.model_construct(**kwargs)


def test_langfuse_prompt_store_satisfies_protocol():
    """Verify LangfusePromptStore conforms to PromptStore protocol."""
    store = LangfusePromptStore(settings=_make_settings(langfuse_enabled=False))
    assert isinstance(store, PromptStore)


def test_langfuse_prompt_store_remote_retrieval_and_metadata():
    """Verify successful remote prompt fetch links langfuse_prompt in config['metadata']."""
    mock_client = MagicMock()
    mock_prompt = MagicMock(spec=TextPromptClient)
    mock_prompt.name = "rag:direct_answer"
    mock_prompt.prompt = "Answer directly and concisely to: {{question}}"
    mock_prompt.is_fallback = False
    mock_client.get_prompt.return_value = mock_prompt

    settings = _make_settings(langfuse_enabled=True, langfuse_prompt_cache_ttl_seconds=300)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    inbound_config: RunnableConfig = {
        "metadata": {"session_id": "ses-999"},
        "tags": ["unit-test"],
    }
    rendered = store.render(
        "rag:direct_answer",
        config=inbound_config,
        question="How do I connect to VPN?",
    )

    assert isinstance(rendered, RenderedPrompt)
    assert rendered.name == "rag:direct_answer"
    assert rendered.text == "Answer directly and concisely to: How do I connect to VPN?"
    assert rendered.raw_prompt is mock_prompt
    assert rendered.config["tags"] == ["unit-test"]
    assert rendered.config["metadata"]["session_id"] == "ses-999"
    assert rendered.config["metadata"]["langfuse_prompt"] is mock_prompt
    assert "prompt_fallback" not in rendered.config["metadata"]


def test_langfuse_prompt_store_get_template_remote():
    """Verify get_template retrieves remote template text when available."""
    mock_client = MagicMock()
    mock_prompt = MagicMock(spec=TextPromptClient)
    mock_prompt.prompt = "Custom remote template for {{question}}"
    mock_prompt.is_fallback = False
    mock_client.get_prompt.return_value = mock_prompt

    settings = _make_settings(langfuse_enabled=True)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    template = store.get_template("rag:direct_answer")
    assert template == "Custom remote template for {{question}}"


def test_langfuse_prompt_store_in_memory_ttl_caching():
    """Verify in-memory TTL cache prevents duplicate network calls within TTL window."""
    mock_client = MagicMock()
    mock_prompt = MagicMock(spec=TextPromptClient)
    mock_prompt.prompt = "TTL Cached template: {{question}}"
    mock_prompt.is_fallback = False
    mock_client.get_prompt.return_value = mock_prompt

    settings = _make_settings(langfuse_enabled=True, langfuse_prompt_cache_ttl_seconds=100)
    store = LangfusePromptStore(settings=settings, client=mock_client, cache_ttl_seconds=100)

    # First call - queries client
    res1 = store.render("rag:direct_answer", question="q1")
    assert mock_client.get_prompt.call_count == 1
    assert res1.text == "TTL Cached template: q1"

    # Second call within TTL - served from in-memory cache
    res2 = store.render("rag:direct_answer", question="q2")
    assert mock_client.get_prompt.call_count == 1
    assert res2.text == "TTL Cached template: q2"

    # get_template also served from in-memory cache
    tpl = store.get_template("rag:direct_answer")
    assert mock_client.get_prompt.call_count == 1
    assert tpl == "TTL Cached template: {{question}}"


def test_langfuse_prompt_store_ttl_expiration(monkeypatch: pytest.MonkeyPatch):
    """Verify prompt is refetched after TTL expires."""
    import time

    mock_client = MagicMock()
    mock_prompt1 = MagicMock(spec=TextPromptClient)
    mock_prompt1.prompt = "Initial template: {{question}}"
    mock_prompt1.is_fallback = False

    mock_prompt2 = MagicMock(spec=TextPromptClient)
    mock_prompt2.prompt = "Refreshed template: {{question}}"
    mock_prompt2.is_fallback = False

    mock_client.get_prompt.side_effect = [mock_prompt1, mock_prompt2]

    fake_time = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_time)

    settings = _make_settings(langfuse_enabled=True, langfuse_prompt_cache_ttl_seconds=30)
    store = LangfusePromptStore(settings=settings, client=mock_client, cache_ttl_seconds=30)

    res1 = store.render("rag:direct_answer", question="first")
    assert res1.text == "Initial template: first"
    assert mock_client.get_prompt.call_count == 1

    # Advance time beyond TTL
    fake_time = 1035.0
    res2 = store.render("rag:direct_answer", question="second")
    assert res2.text == "Refreshed template: second"
    assert mock_client.get_prompt.call_count == 2


def test_langfuse_prompt_store_zero_ttl_bypasses_cache():
    """Verify cache_ttl_seconds=0 disables in-memory caching."""
    mock_client = MagicMock()
    mock_prompt = MagicMock(spec=TextPromptClient)
    mock_prompt.prompt = "Template: {{question}}"
    mock_prompt.is_fallback = False
    mock_client.get_prompt.return_value = mock_prompt

    settings = _make_settings(langfuse_enabled=True, langfuse_prompt_cache_ttl_seconds=0)
    store = LangfusePromptStore(settings=settings, client=mock_client, cache_ttl_seconds=0)

    store.render("rag:direct_answer", question="q1")
    store.render("rag:direct_answer", question="q2")
    assert mock_client.get_prompt.call_count == 2


def test_langfuse_prompt_store_disabled_fail_open_fallback():
    """Verify store falls back to local templates when Langfuse is disabled."""
    mock_client = MagicMock()
    settings = _make_settings(langfuse_enabled=False)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    rendered = store.render("rag:direct_answer", question="How do I reset my password?")
    mock_client.get_prompt.assert_not_called()

    assert rendered.raw_prompt is None
    assert (
        rendered.text
        == "Respond briefly, warmly, and naturally as an enterprise IT support copilot to: How do I reset my password?"
    )
    assert rendered.config["metadata"] == {
        "prompt_name": "rag:direct_answer",
        "prompt_fallback": True,
    }


def test_langfuse_prompt_store_none_client_fail_open_fallback(monkeypatch: pytest.MonkeyPatch):
    """Verify store falls back to local templates when Langfuse client is None."""
    monkeypatch.setattr("app.rag.prompts.langfuse.get_langfuse_client", lambda *args, **kwargs: None)
    settings = _make_settings(langfuse_enabled=True)
    store = LangfusePromptStore(settings=settings, client=None)

    rendered = store.render("rag:direct_answer", question="How do I request software?")
    assert rendered.raw_prompt is None
    assert rendered.config["metadata"]["prompt_fallback"] is True
    assert rendered.config["metadata"]["prompt_name"] == "rag:direct_answer"


def test_langfuse_prompt_store_network_error_fail_open_fallback():
    """Verify network exceptions gracefully fail open to local templates with fallback metadata."""
    mock_client = MagicMock()
    mock_client.get_prompt.side_effect = ConnectionError("Langfuse service unavailable")

    settings = _make_settings(langfuse_enabled=True)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    rendered = store.render(
        "rag:generate_kb",
        question="VPN steps",
        context="Use Cisco AnyConnect.",
    )

    assert rendered.raw_prompt is None
    assert "Private KB:\nUse Cisco AnyConnect." in rendered.text
    assert rendered.config["metadata"]["prompt_fallback"] is True
    assert rendered.config["metadata"]["prompt_name"] == "rag:generate_kb"


def test_langfuse_prompt_store_is_fallback_flag_triggers_fallback_metadata():
    """Verify Langfuse returning a fallback prompt object triggers fallback metadata."""
    mock_client = MagicMock()
    mock_prompt = MagicMock(spec=TextPromptClient)
    mock_prompt.is_fallback = True
    mock_client.get_prompt.return_value = mock_prompt

    settings = _make_settings(langfuse_enabled=True)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    rendered = store.render("rag:direct_answer", question="Hello")
    assert rendered.raw_prompt is None
    assert rendered.config["metadata"]["prompt_fallback"] is True
    assert rendered.config["metadata"]["prompt_name"] == "rag:direct_answer"


def test_langfuse_prompt_store_seed_prompts_when_already_exist():
    """Verify seed_prompts checks server with cache_ttl_seconds=0 and skips create_prompt."""
    mock_client = MagicMock()
    mock_existing = MagicMock(spec=TextPromptClient)
    mock_existing.is_fallback = False
    mock_existing.version = 2
    mock_client.get_prompt.return_value = mock_existing

    settings = _make_settings(langfuse_enabled=True)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    results = store.seed_prompts()
    assert len(results) == len(CANONICAL_PROMPTS)
    assert all(results.values())

    # Checked server with cache_ttl_seconds=0
    mock_client.get_prompt.assert_any_call(
        name="rag:direct_answer",
        label="production",
        cache_ttl_seconds=0,
    )
    # create_prompt must NOT have been called
    mock_client.create_prompt.assert_not_called()

    # Second call should be a complete in-memory no-op
    mock_client.get_prompt.reset_mock()
    results2 = store.seed_prompts()
    assert len(results2) == len(CANONICAL_PROMPTS)
    assert all(results2.values())
    mock_client.get_prompt.assert_not_called()
    mock_client.create_prompt.assert_not_called()


def test_langfuse_prompt_store_seed_prompts_creates_missing():
    """Verify seed_prompts calls create_prompt with label 'production' when prompt is missing."""
    mock_client = MagicMock()
    # Remote check fails (e.g. 404 LangfuseNotFoundError)
    mock_client.get_prompt.side_effect = RuntimeError("404 Prompt not found")

    settings = _make_settings(langfuse_enabled=True)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    results = store.seed_prompts()
    assert len(results) == len(CANONICAL_PROMPTS)
    assert all(results.values())

    assert mock_client.create_prompt.call_count == len(CANONICAL_PROMPTS)
    mock_client.create_prompt.assert_any_call(
        name="rag:direct_answer",
        prompt=CANONICAL_PROMPTS["rag:direct_answer"],
        labels=["production"],
        type="text",
    )

    # In-memory idempotency check: subsequent call doesn't call create_prompt again
    mock_client.create_prompt.reset_mock()
    results2 = store.seed_prompts()
    assert all(results2.values())
    mock_client.create_prompt.assert_not_called()


def test_langfuse_prompt_store_seed_prompts_when_disabled():
    """Verify seed_prompts safely handles disabled Langfuse without network calls."""
    mock_client = MagicMock()
    settings = _make_settings(langfuse_enabled=False)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    results = store.seed_prompts()
    assert len(results) == len(CANONICAL_PROMPTS)
    assert not any(results.values())
    mock_client.get_prompt.assert_not_called()
    mock_client.create_prompt.assert_not_called()


def test_langfuse_prompt_store_seed_prompts_failure_handling():
    """Verify seed_prompts handles create_prompt exceptions gracefully."""
    mock_client = MagicMock()
    mock_client.get_prompt.side_effect = RuntimeError("404 Prompt not found")
    mock_client.create_prompt.side_effect = RuntimeError("500 Server error")

    settings = _make_settings(langfuse_enabled=True)
    store = LangfusePromptStore(settings=settings, client=mock_client)

    results = store.seed_prompts()
    assert len(results) == len(CANONICAL_PROMPTS)
    assert not any(results.values())


def test_get_prompt_store_and_reset():
    """Verify dependency injection factory and reset lifecycle."""
    from app.rag.prompts import (
        LangfusePromptStore,
        LocalPromptStore,
        get_prompt_store,
        reset_prompt_store,
    )

    reset_prompt_store()
    try:
        # Default singleton
        store1 = get_prompt_store()
        assert isinstance(store1, LangfusePromptStore)
        store2 = get_prompt_store()
        assert store1 is store2

        # Explicit local store
        local_store = get_prompt_store(store_type="local")
        assert isinstance(local_store, LocalPromptStore)

        # Invalid store type
        with pytest.raises(ValueError, match="Unsupported prompt store type"):
            get_prompt_store(store_type="unsupported_type")

        # Injected store override
        custom_store = LocalPromptStore()
        injected = get_prompt_store(store=custom_store)
        assert injected is custom_store
        assert get_prompt_store() is custom_store

        # Reset clears singleton
        reset_prompt_store()
        fresh_store = get_prompt_store()
        assert fresh_store is not custom_store
    finally:
        reset_prompt_store()


def test_fake_prompt_store_fixture(fake_prompt_store: LocalPromptStore):
    """Verify fake_prompt_store fixture injects clean LocalPromptStore into get_prompt_store."""
    from app.rag.prompts import get_prompt_store

    active_store = get_prompt_store()
    assert active_store is fake_prompt_store
    assert isinstance(active_store, LocalPromptStore)
