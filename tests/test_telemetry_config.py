import pytest
from pydantic import SecretStr

from app.core.config import Settings


def test_default_langfuse_settings(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)

    settings = Settings(
        _env_file=None,
        api_key="dummy-api-key",
        tavily_api_key="dummy-tavily-key",
        pinecone_api_key="dummy-pinecone-key",
        admin_api_key="dummy-admin-key",
    )

    assert settings.langfuse_enabled is True
    assert settings.langfuse_host == "https://cloud.langfuse.com"
    assert settings.langfuse_public_key is None
    assert settings.langfuse_secret_key is None


def test_custom_langfuse_settings(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-123")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-456")
    monkeypatch.setenv("LANGFUSE_HOST", "https://my-langfuse.example.com")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    settings = Settings(
        api_key="dummy-api-key",
        tavily_api_key="dummy-tavily-key",
        pinecone_api_key="dummy-pinecone-key",
        admin_api_key="dummy-admin-key",
    )

    assert settings.langfuse_public_key == "pk-123"
    assert isinstance(settings.langfuse_secret_key, SecretStr)
    assert settings.langfuse_secret_key.get_secret_value() == "sk-456"
    assert settings.langfuse_host == "https://my-langfuse.example.com"
    assert settings.langfuse_enabled is False
