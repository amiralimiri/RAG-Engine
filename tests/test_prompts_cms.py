import pytest
from unittest.mock import MagicMock, patch

from langfuse.model import TextPromptClient
from app.services.prompts import FallbackPrompt, LOCAL_PROMPTS, get_workflow_prompt


class TestFallbackPrompt:
    def test_fallback_prompt_initialization_and_properties(self):
        prompt = FallbackPrompt(name="test:prompt", template="Hello {{name}}!", version=0)
        assert prompt.name == "test:prompt"
        assert prompt.template == "Hello {{name}}!"
        assert prompt.version == 0
        assert prompt.prompt == "Hello {{name}}!"

    def test_fallback_prompt_compile_single_variable(self):
        prompt = FallbackPrompt(name="greeting", template="Hello, {{name}}!")
        rendered = prompt.compile(name="Alice")
        assert rendered == "Hello, Alice!"

    def test_fallback_prompt_compile_multiple_variables_with_spacing(self):
        template = "Question: {{ question }}\nContext:\n{{context}}\nQuery: {{  current_query  }}"
        prompt = FallbackPrompt(name="multi_var", template=template)
        rendered = prompt.compile(
            question="How do I reset MFA?",
            context="Use the IT portal.",
            current_query="mfa reset",
        )
        assert rendered == "Question: How do I reset MFA?\nContext:\nUse the IT portal.\nQuery: mfa reset"

    def test_fallback_prompt_compile_handles_backslashes_in_values(self):
        prompt = FallbackPrompt(name="path_test", template="File path: {{path}}")
        rendered = prompt.compile(path=r"C:\users\alice\documents\file.txt")
        assert rendered == r"File path: C:\users\alice\documents\file.txt"

    def test_fallback_prompt_compile_ignores_missing_variables(self):
        prompt = FallbackPrompt(name="partial", template="Hello {{name}}, welcome to {{place}}!")
        rendered = prompt.compile(name="Bob")
        assert rendered == "Hello Bob, welcome to {{place}}!"


class TestLocalPromptsRegistry:
    REQUIRED_KEYS = [
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
    ]

    def test_all_canonical_prompt_keys_exist(self):
        for key in self.REQUIRED_KEYS:
            assert key in LOCAL_PROMPTS, f"Missing required canonical prompt key: {key}"
            assert isinstance(LOCAL_PROMPTS[key], str)
            assert len(LOCAL_PROMPTS[key].strip()) > 0

    def test_all_canonical_prompts_compile_cleanly(self):
        test_vars = {
            "question": "What is the VPN server address?",
            "context": "The VPN server is vpn.company.com.",
            "current_query": "vpn address",
            "web_results": "Title: VPN\nContent: Connect to VPN gateway.",
        }
        for key in self.REQUIRED_KEYS:
            prompt = FallbackPrompt(name=key, template=LOCAL_PROMPTS[key])
            rendered = prompt.compile(**test_vars)
            assert isinstance(rendered, str)
            assert "{{" not in rendered, f"Unsubstituted mustache tag remaining in {key}: {rendered}"


class TestGetWorkflowPrompt:
    def test_get_workflow_prompt_langfuse_disabled_returns_fallback(self):
        with patch("app.services.prompts.get_settings") as mock_settings:
            mock_settings.return_value.langfuse_enabled = False
            mock_settings.return_value.langfuse_prompt_cache_ttl_seconds = 300

            prompt = get_workflow_prompt("rag:direct_answer")
            assert isinstance(prompt, FallbackPrompt)
            assert prompt.name == "rag:direct_answer"
            assert prompt.version == 0
            assert "Respond briefly, warmly" in prompt.template
            rendered = prompt.compile(question="hello")
            assert "hello" in rendered

    def test_get_workflow_prompt_langfuse_client_none_returns_fallback(self):
        with patch("app.services.prompts.get_settings") as mock_settings, \
             patch("app.services.prompts.get_langfuse_client", return_value=None):
            mock_settings.return_value.langfuse_enabled = True
            mock_settings.return_value.langfuse_prompt_cache_ttl_seconds = 300

            prompt = get_workflow_prompt("rag:router:llm")
            assert isinstance(prompt, FallbackPrompt)
            assert prompt.name == "rag:router:llm"
            assert prompt.version == 0
            assert "You route messages" in prompt.template

    def test_get_workflow_prompt_client_exception_fail_open(self):
        mock_client = MagicMock()
        mock_client.get_prompt.side_effect = ConnectionError("Langfuse unreachable")

        with patch("app.services.prompts.get_settings") as mock_settings, \
             patch("app.services.prompts.get_langfuse_client", return_value=mock_client):
            mock_settings.return_value.langfuse_enabled = True
            mock_settings.return_value.langfuse_prompt_cache_ttl_seconds = 300

            prompt = get_workflow_prompt("rag:generate_kb")
            assert isinstance(prompt, FallbackPrompt)
            assert prompt.name == "rag:generate_kb"
            assert prompt.version == 0
            assert "Private KB" in prompt.template

    def test_get_workflow_prompt_success_calls_langfuse_client(self):
        mock_client = MagicMock()
        mock_langfuse_prompt = MagicMock(spec=TextPromptClient)
        mock_langfuse_prompt.name = "rag:direct_answer"
        mock_langfuse_prompt.version = 4
        mock_client.get_prompt.return_value = mock_langfuse_prompt

        with patch("app.services.prompts.get_settings") as mock_settings, \
             patch("app.services.prompts.get_langfuse_client", return_value=mock_client):
            mock_settings.return_value.langfuse_enabled = True
            mock_settings.return_value.langfuse_prompt_cache_ttl_seconds = 300

            prompt = get_workflow_prompt("rag:direct_answer")

            assert prompt is mock_langfuse_prompt
            mock_client.get_prompt.assert_called_once_with(
                name="rag:direct_answer",
                type="text",
                cache_ttl_seconds=300,
                fallback=LOCAL_PROMPTS["rag:direct_answer"],
            )

    def test_get_workflow_prompt_unknown_key_returns_empty_fallback(self):
        with patch("app.services.prompts.get_settings") as mock_settings:
            mock_settings.return_value.langfuse_enabled = False
            mock_settings.return_value.langfuse_prompt_cache_ttl_seconds = 300

            prompt = get_workflow_prompt("rag:unknown_key")
            assert isinstance(prompt, FallbackPrompt)
            assert prompt.name == "rag:unknown_key"
            assert prompt.template == ""
            assert prompt.version == 0


class TestPromptAutoSeeding:
    """Verifies auto-seeding missing prompts into Langfuse Prompt CMS on 404 or fallback."""

    def test_seed_prompt_calls_client_create_prompt(self, monkeypatch):
        from app.services import prompts
        prompts._seeded_prompts.clear()

        mock_client = MagicMock()
        mock_settings = MagicMock()
        mock_settings.langfuse_enabled = True

        monkeypatch.setattr(prompts, "get_settings", lambda: mock_settings)
        monkeypatch.setattr(prompts, "get_langfuse_client", lambda: mock_client)

        success = prompts.seed_prompt("rag:direct_answer")
        assert success is True
        mock_client.create_prompt.assert_called_once_with(
            name="rag:direct_answer",
            prompt=prompts.LOCAL_PROMPTS["rag:direct_answer"],
            labels=["production"],
            type="text",
        )
        assert "rag:direct_answer" in prompts._seeded_prompts

        # Second call should be a no-op (idempotent per process)
        mock_client.create_prompt.reset_mock()
        success2 = prompts.seed_prompt("rag:direct_answer")
        assert success2 is True
        mock_client.create_prompt.assert_not_called()

    def test_seed_all_prompts_seeds_all_ten_prompts(self, monkeypatch):
        from app.services import prompts
        prompts._seeded_prompts.clear()

        mock_client = MagicMock()
        mock_settings = MagicMock()
        mock_settings.langfuse_enabled = True

        monkeypatch.setattr(prompts, "get_settings", lambda: mock_settings)
        monkeypatch.setattr(prompts, "get_langfuse_client", lambda: mock_client)

        results = prompts.seed_all_prompts()
        assert len(results) == 10
        assert all(results.values())
        assert mock_client.create_prompt.call_count == 10

    def test_get_workflow_prompt_auto_seeds_when_fallback_returned(self, monkeypatch):
        from app.services import prompts
        prompts._seeded_prompts.clear()

        mock_client = MagicMock()
        mock_fallback_client = MagicMock(spec=TextPromptClient)
        mock_fallback_client.is_fallback = True
        mock_client.get_prompt.return_value = mock_fallback_client

        mock_settings = MagicMock()
        mock_settings.langfuse_enabled = True
        mock_settings.langfuse_prompt_cache_ttl_seconds = 300

        monkeypatch.setattr(prompts, "get_settings", lambda: mock_settings)
        monkeypatch.setattr(prompts, "get_langfuse_client", lambda: mock_client)

        prompt = prompts.get_workflow_prompt("rag:router:llm")
        assert prompt is mock_fallback_client
        mock_client.create_prompt.assert_called_once_with(
            name="rag:router:llm",
            prompt=prompts.LOCAL_PROMPTS["rag:router:llm"],
            labels=["production"],
            type="text",
        )

    def test_get_workflow_prompt_auto_seeds_on_404_exception(self, monkeypatch):
        from app.services import prompts
        prompts._seeded_prompts.clear()

        mock_client = MagicMock()
        mock_client.get_prompt.side_effect = RuntimeError("404 LangfuseNotFoundError")

        mock_settings = MagicMock()
        mock_settings.langfuse_enabled = True
        mock_settings.langfuse_prompt_cache_ttl_seconds = 300

        monkeypatch.setattr(prompts, "get_settings", lambda: mock_settings)
        monkeypatch.setattr(prompts, "get_langfuse_client", lambda: mock_client)

        prompt = prompts.get_workflow_prompt("rag:grade_kb:jev")
        assert isinstance(prompt, prompts.FallbackPrompt)
        mock_client.create_prompt.assert_called_once_with(
            name="rag:grade_kb:jev",
            prompt=prompts.LOCAL_PROMPTS["rag:grade_kb:jev"],
            labels=["production"],
            type="text",
        )

