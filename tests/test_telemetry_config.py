import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings


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


def test_default_evaluation_and_cost_settings():
    settings = Settings()

    # Evaluation defaults
    assert settings.eval_sample_rate == 1.0
    assert settings.eval_metrics == ["faithfulness", "answer_relevancy"]
    assert settings.eval_timeout_seconds == 30.0
    assert settings.langfuse_prompt_cache_ttl_seconds == 300

    # Token cost multipliers (USD per 1k tokens)
    assert settings.llm_cost_per_1k_input_tokens == 0.0015
    assert settings.llm_cost_per_1k_output_tokens == 0.0020
    assert settings.jev_cost_per_1k_input_tokens == 0.0008
    assert settings.jev_cost_per_1k_output_tokens == 0.0008

    # Ensure obsolete SQLite audit db path is removed
    assert not hasattr(settings, "audit_db_path")


def test_eval_sample_rate_validation():
    # Valid boundaries
    assert Settings(eval_sample_rate=0.0).eval_sample_rate == 0.0
    assert Settings(eval_sample_rate=0.5).eval_sample_rate == 0.5
    assert Settings(eval_sample_rate=1.0).eval_sample_rate == 1.0

    # Invalid values
    with pytest.raises(ValidationError):
        Settings(eval_sample_rate=-0.01)

    with pytest.raises(ValidationError):
        Settings(eval_sample_rate=1.01)

    with pytest.raises(ValidationError):
        Settings(eval_sample_rate="not_a_float")


def test_eval_metrics_validation():
    assert Settings(eval_metrics=["custom_metric"]).eval_metrics == ["custom_metric"]
    assert Settings(eval_metrics=[]).eval_metrics == []

    with pytest.raises(ValidationError):
        Settings(eval_metrics=123)

    with pytest.raises(ValidationError):
        Settings(eval_metrics=[1, 2, 3])


def test_eval_timeout_seconds_validation():
    assert Settings(eval_timeout_seconds=0).eval_timeout_seconds == 0.0
    assert Settings(eval_timeout_seconds=45.5).eval_timeout_seconds == 45.5

    with pytest.raises(ValidationError):
        Settings(eval_timeout_seconds=-1.0)

    with pytest.raises(ValidationError):
        Settings(eval_timeout_seconds="not_a_number")


def test_cost_multipliers_validation():
    # Valid non-negative multipliers
    s = Settings(
        llm_cost_per_1k_input_tokens=0.0,
        llm_cost_per_1k_output_tokens=0.005,
        jev_cost_per_1k_input_tokens=0.001,
        jev_cost_per_1k_output_tokens=0.0012,
    )
    assert s.llm_cost_per_1k_input_tokens == 0.0
    assert s.llm_cost_per_1k_output_tokens == 0.005
    assert s.jev_cost_per_1k_input_tokens == 0.001
    assert s.jev_cost_per_1k_output_tokens == 0.0012

    # Negative values are prohibited
    with pytest.raises(ValidationError):
        Settings(llm_cost_per_1k_input_tokens=-0.001)

    with pytest.raises(ValidationError):
        Settings(llm_cost_per_1k_output_tokens=-0.001)

    with pytest.raises(ValidationError):
        Settings(jev_cost_per_1k_input_tokens=-0.001)

    with pytest.raises(ValidationError):
        Settings(jev_cost_per_1k_output_tokens=-0.001)


def test_env_overrides_for_eval_and_costs(monkeypatch):
    monkeypatch.setenv("EVAL_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("EVAL_METRICS", '["answer_relevancy"]')
    monkeypatch.setenv("EVAL_TIMEOUT_SECONDS", "45.0")
    monkeypatch.setenv("LANGFUSE_PROMPT_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("LLM_COST_PER_1K_INPUT_TOKENS", "0.003")
    monkeypatch.setenv("LLM_COST_PER_1K_OUTPUT_TOKENS", "0.004")
    monkeypatch.setenv("JEV_COST_PER_1K_INPUT_TOKENS", "0.001")
    monkeypatch.setenv("JEV_COST_PER_1K_OUTPUT_TOKENS", "0.0015")

    settings = Settings()

    assert settings.eval_sample_rate == 0.25
    assert settings.eval_metrics == ["answer_relevancy"]
    assert settings.eval_timeout_seconds == 45.0
    assert settings.langfuse_prompt_cache_ttl_seconds == 600
    assert settings.llm_cost_per_1k_input_tokens == 0.003
    assert settings.llm_cost_per_1k_output_tokens == 0.004
    assert settings.jev_cost_per_1k_input_tokens == 0.001
    assert settings.jev_cost_per_1k_output_tokens == 0.0015


def test_eval_metrics_comma_separated_env_override(monkeypatch):
    monkeypatch.setenv("EVAL_METRICS", "faithfulness, answer_relevancy, custom_metric")
    settings = Settings()
    assert settings.eval_metrics == ["faithfulness", "answer_relevancy", "custom_metric"]


def test_get_settings_exposes_defaults():
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.eval_sample_rate == 1.0
    assert settings.eval_metrics == ["faithfulness", "answer_relevancy"]
    assert settings.eval_timeout_seconds == 30.0
    assert settings.langfuse_prompt_cache_ttl_seconds == 300
    assert settings.llm_cost_per_1k_input_tokens == 0.0015
    assert settings.llm_cost_per_1k_output_tokens == 0.0020
    assert settings.jev_cost_per_1k_input_tokens == 0.0008
    assert settings.jev_cost_per_1k_output_tokens == 0.0008
    assert not hasattr(settings, "audit_db_path")
