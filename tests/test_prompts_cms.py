"""Tests for unified PromptStore protocol seam, LocalPromptStore, and LangfusePromptStore CMS integration."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import RunnableConfig
from langfuse.model import TextPromptClient

from app.core.config import Settings
from app.rag.prompts import (
    CANONICAL_PROMPTS,
    LangfusePromptStore,
    LocalPromptStore,
    PromptStore,
    RenderedPrompt,
    get_prompt_store,
    reset_prompt_store,
)


def _make_settings(**kwargs: Any) -> Settings:
    """Helper to instantiate Settings test doubles with arbitrary overrides."""
    return Settings.model_construct(**kwargs)


class TestPromptStoreProtocol:
    """Verify runtime checkability and protocol compliance of adapters."""

    def test_local_store_satisfies_protocol(self):
        store = LocalPromptStore()
        assert isinstance(store, PromptStore)

    def test_langfuse_store_satisfies_protocol(self):
        settings = _make_settings(langfuse_enabled=False)
        store = LangfusePromptStore(settings=settings)
        assert isinstance(store, PromptStore)


class TestRenderedPromptValueObject:
    """Verify RenderedPrompt domain model behavior, string coercion, and config merging."""

    def test_rendered_prompt_str_coercion(self):
        prompt = RenderedPrompt(name="greeting", text="Hello world")
        assert prompt.name == "greeting"
        assert prompt.text == "Hello world"
        assert str(prompt) == "Hello world"
        assert f"{prompt}" == "Hello world"
        assert prompt.config == {}
        assert prompt.raw_prompt is None

    def test_rendered_prompt_custom_config_and_raw_prompt(self):
        raw_obj = object()
        cfg = {"metadata": {"trace_id": "tr-123"}, "tags": ["test"]}
        prompt = RenderedPrompt(name="custom", text="Custom text", config=cfg, raw_prompt=raw_obj)
        assert prompt.config["metadata"]["trace_id"] == "tr-123"
        assert prompt.raw_prompt is raw_obj


class TestLocalPromptStore:
    """Verify LocalPromptStore in-memory adapter, canonical templates, and interpolation."""

    REQUIRED_CANONICAL_KEYS = [
        "rag:router:llm",
        "rag:router:jev",
        "rag:grade_kb:llm",
        "rag:grade_kb:jev",
        "rag:grade_web:llm",
        "rag:grade_web:jev",
        "rag:rewrite_query",
        "rag:generate_kb",
        "rag:generate_web",
        "rag:direct_answer",
        "rag:condense_query",
    ]

    def test_all_canonical_prompt_keys_exist(self):
        store = LocalPromptStore()
        for key in self.REQUIRED_CANONICAL_KEYS:
            assert key in CANONICAL_PROMPTS
            template = store.get_template(key)
            assert isinstance(template, str)
            assert len(template.strip()) > 0

    def test_all_canonical_prompts_render_cleanly(self):
        store = LocalPromptStore()
        test_vars = {
            "question": "What is the VPN server address?",
            "context": "The VPN server is vpn.company.com.",
            "current_query": "vpn address",
            "web_results": "Title: VPN\nContent: Connect to VPN gateway.",
            "chat_history": "Human: Hi\nAssistant: Hello! How can I help you?",
        }
        for key in self.REQUIRED_CANONICAL_KEYS:
            rendered = store.render(key, **test_vars)
            assert isinstance(rendered, RenderedPrompt)
            assert "{{" not in rendered.text, f"Unsubstituted mustache tag remaining in {key}: {rendered.text}"

    def test_template_interpolation_whitespace_and_backslashes(self):
        store = LocalPromptStore(
            templates={
                "path_test": "File: {{path}}",
                "spacing_test": "Question: {{ question }}\nQuery: {{  current_query  }}",
                "partial_test": "Hello {{user}}, welcome to {{place}}!",
            }
        )

        path_val = r"C:\users\alice\documents\file.txt"
        assert store.render("path_test", path=path_val).text == f"File: {path_val}"

        rendered_spacing = store.render("spacing_test", question="How to MFA?", current_query="mfa")
        assert rendered_spacing.text == "Question: How to MFA?\nQuery: mfa"

        rendered_partial = store.render("partial_test", user="Bob")
        assert rendered_partial.text == "Hello Bob, welcome to {{place}}!"

    def test_custom_template_registration_and_retrieval(self):
        store = LocalPromptStore(templates={"custom:one": "One {{val}}"})
        assert store.get_template("custom:one") == "One {{val}}"

        with pytest.raises(KeyError):
            store.get_template("rag:direct_answer")

        store.register_prompt("custom:two", "Two {{val}}")
        assert store.get_template("custom:two") == "Two {{val}}"
        assert store.render("custom:two", val="2").text == "Two 2"

    def test_unknown_key_raises_key_error(self):
        store = LocalPromptStore()
        with pytest.raises(KeyError, match="not found"):
            store.get_template("nonexistent:key")
        with pytest.raises(KeyError, match="not found"):
            store.render("nonexistent:key")

    def test_render_merges_inbound_runnable_config(self):
        store = LocalPromptStore()
        inbound_cfg: RunnableConfig = {
            "metadata": {"session_id": "ses-abc", "user_id": "usr-1"},
            "tags": ["prod"],
            "run_name": "agent_run",
        }
        rendered = store.render("rag:direct_answer", config=inbound_cfg, question="Hello")

        assert rendered.config["tags"] == ["prod"]
        assert rendered.config["run_name"] == "agent_run"
        assert rendered.config["metadata"] == {
            "session_id": "ses-abc",
            "user_id": "usr-1",
            "prompt_name": "rag:direct_answer",
            "prompt_fallback": True,
        }

    def test_seed_prompts_reports_all_seeded(self):
        store = LocalPromptStore(templates={"p1": "one", "p2": "two"})
        result = store.seed_prompts()
        assert result == {"p1": True, "p2": True}


class TestLangfusePromptStoreRetrieval:
    """Verify LangfusePromptStore remote resolution, fallback mechanisms, and caching."""

    def test_remote_prompt_retrieval_success(self):
        mock_client = MagicMock()
        mock_prompt = MagicMock(spec=TextPromptClient)
        mock_prompt.name = "rag:direct_answer"
        mock_prompt.prompt = "Answer directly to: {{question}}"
        mock_prompt.is_fallback = False
        mock_client.get_prompt.return_value = mock_prompt

        settings = _make_settings(langfuse_enabled=True, langfuse_prompt_cache_ttl_seconds=300)
        store = LangfusePromptStore(settings=settings, client=mock_client)

        inbound_config: RunnableConfig = {"metadata": {"session_id": "ses-100"}, "tags": ["live"]}
        rendered = store.render("rag:direct_answer", config=inbound_config, question="Need help")

        assert rendered.name == "rag:direct_answer"
        assert rendered.text == "Answer directly to: Need help"
        assert rendered.raw_prompt is mock_prompt
        assert rendered.config["tags"] == ["live"]
        assert rendered.config["metadata"]["session_id"] == "ses-100"
        assert rendered.config["metadata"]["langfuse_prompt"] is mock_prompt
        assert "prompt_fallback" not in rendered.config["metadata"]
        mock_client.get_prompt.assert_called_once_with(
            name="rag:direct_answer",
            label="production",
            cache_ttl_seconds=300,
        )

    def test_langfuse_disabled_falls_back_to_local(self):
        settings = _make_settings(langfuse_enabled=False)
        store = LangfusePromptStore(settings=settings)

        rendered = store.render("rag:direct_answer", question="hello")
        assert rendered.name == "rag:direct_answer"
        assert "Respond briefly, warmly" in rendered.text
        assert rendered.config["metadata"]["prompt_fallback"] is True
        assert rendered.config["metadata"]["prompt_name"] == "rag:direct_answer"
        assert rendered.raw_prompt is None

    def test_langfuse_client_none_falls_back_to_local(self):
        settings = _make_settings(langfuse_enabled=True)
        store = LangfusePromptStore(settings=settings, client=None)

        rendered = store.render("rag:router:llm", question="vpn issue")
        assert rendered.name == "rag:router:llm"
        assert "You route messages" in rendered.text
        assert rendered.config["metadata"]["prompt_fallback"] is True

    def test_client_exception_fails_open_to_local(self):
        mock_client = MagicMock()
        mock_client.get_prompt.side_effect = ConnectionError("Langfuse unreachable")

        settings = _make_settings(langfuse_enabled=True)
        store = LangfusePromptStore(settings=settings, client=mock_client)

        rendered = store.render("rag:generate_kb", question="vpn", context="portal")
        assert rendered.name == "rag:generate_kb"
        assert "Private KB" in rendered.text
        assert rendered.config["metadata"]["prompt_fallback"] is True

    def test_ttl_caching_and_expiration(self, monkeypatch: pytest.MonkeyPatch):
        mock_client = MagicMock()
        mock_prompt1 = MagicMock(spec=TextPromptClient)
        mock_prompt1.prompt = "Cached template: {{question}}"
        mock_prompt1.is_fallback = False

        mock_prompt2 = MagicMock(spec=TextPromptClient)
        mock_prompt2.prompt = "Refreshed template: {{question}}"
        mock_prompt2.is_fallback = False

        mock_client.get_prompt.side_effect = [mock_prompt1, mock_prompt2]

        current_time = 1000.0
        monkeypatch.setattr(time, "monotonic", lambda: current_time)

        settings = _make_settings(langfuse_enabled=True, langfuse_prompt_cache_ttl_seconds=10)
        store = LangfusePromptStore(settings=settings, client=mock_client, cache_ttl_seconds=10)

        # Call 1: fetch from client
        res1 = store.render("rag:direct_answer", question="q1")
        assert mock_client.get_prompt.call_count == 1
        assert res1.text == "Cached template: q1"

        # Call 2: served from cache within TTL
        res2 = store.render("rag:direct_answer", question="q2")
        assert mock_client.get_prompt.call_count == 1
        assert res2.text == "Cached template: q2"

        # Advance time beyond TTL
        current_time = 1020.0
        res3 = store.render("rag:direct_answer", question="q3")
        assert mock_client.get_prompt.call_count == 2
        assert res3.text == "Refreshed template: q3"


class TestLangfusePromptStoreAutoSeeding:
    """Verify idempotent seeding of missing prompts into Langfuse Prompt CMS."""

    def test_seed_prompts_skips_when_already_exists(self):
        mock_client = MagicMock()
        mock_existing = MagicMock(spec=TextPromptClient)
        mock_existing.is_fallback = False
        mock_client.get_prompt.return_value = mock_existing

        settings = _make_settings(langfuse_enabled=True)
        store = LangfusePromptStore(settings=settings, client=mock_client)

        results = store.seed_prompts()
        assert len(results) == len(CANONICAL_PROMPTS)
        assert all(results.values())
        mock_client.create_prompt.assert_not_called()

    def test_seed_prompts_creates_missing_prompts(self):
        mock_client = MagicMock()
        mock_client.get_prompt.side_effect = RuntimeError("404 NotFound")

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

        # Re-calling seed_prompts is idempotent (in-memory _seeded set prevents re-query)
        mock_client.create_prompt.reset_mock()
        mock_client.get_prompt.reset_mock()
        results2 = store.seed_prompts()
        assert all(results2.values())
        mock_client.get_prompt.assert_not_called()
        mock_client.create_prompt.assert_not_called()

    def test_seed_prompts_when_disabled_makes_no_calls(self):
        mock_client = MagicMock()
        settings = _make_settings(langfuse_enabled=False)
        store = LangfusePromptStore(settings=settings, client=mock_client)

        results = store.seed_prompts()
        assert len(results) == len(CANONICAL_PROMPTS)
        assert not any(results.values())
        mock_client.get_prompt.assert_not_called()
        mock_client.create_prompt.assert_not_called()

    def test_render_does_not_seed_and_fails_open_on_missing_prompt(self):
        """Verify inference calls perform zero writes (fail-open) per ADR-0018."""
        mock_client = MagicMock()
        mock_client.get_prompt.side_effect = RuntimeError("404 NotFound")

        settings = _make_settings(langfuse_enabled=True, langfuse_prompt_cache_ttl_seconds=300)
        store = LangfusePromptStore(settings=settings, client=mock_client)

        rendered = store.render("rag:grade_kb:jev", question="q", context="c")
        assert rendered.name == "rag:grade_kb:jev"
        assert rendered.config["metadata"]["prompt_fallback"] is True
        mock_client.create_prompt.assert_not_called()


class TestPromptStoreDependencyInjection:
    """Verify get_prompt_store factory, singleton lifecycles, and overrides."""

    def test_get_prompt_store_singleton_and_reset(self):
        reset_prompt_store()
        store1 = get_prompt_store()
        store2 = get_prompt_store()
        assert store1 is store2
        assert isinstance(store1, LangfusePromptStore)

        reset_prompt_store()
        store3 = get_prompt_store()
        assert store3 is not store1
        reset_prompt_store()

    def test_get_prompt_store_explicit_store_injection(self):
        reset_prompt_store()
        custom_store = LocalPromptStore(templates={"custom": "Hello"})
        injected = get_prompt_store(store=custom_store)
        assert injected is custom_store
        assert get_prompt_store() is custom_store

    def test_get_prompt_store_type_selection(self):
        reset_prompt_store()
        local_store = get_prompt_store(store_type="local")
        assert isinstance(local_store, LocalPromptStore)
