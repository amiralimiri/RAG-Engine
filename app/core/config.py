from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )
    
    app_name: str = "Enterprise IT Support Agentic RAG"
    app_env: str = "development"
    
    llm_base_url: str = "https://api.gapgpt.app/v1"
    llm_api_key: SecretStr 
    llm_model: str = "deepseek-v4-flash"
    
    embed_base_url: str = "https://api.gapgpt.app/v1"
    embed_api_key: SecretStr 
    embed_model: str = "text-embedding-3-large"
    
    
    jev_base_url: str = "https://api.gapgpt.app"
    jev_api_key: SecretStr 
    jev_model: str = "typesafe/jev-1.13"
    
    
    tavily_api_key: SecretStr
    pinecone_api_key: SecretStr
    admin_api_key: SecretStr
    
    pinecone_index_name: str = "industry-agentic-rag-kb"
    pinecone_namespace: str = "company-it-kb"
    
    top_k: int = 4
    max_retries: int = 1

    # Chunking & Ingestion Settings
    chunk_size: int = Field(default=900, ge=1)
    chunk_overlap: int = Field(default=120, ge=0)

    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = True
    langfuse_prompt_cache_ttl_seconds: int = Field(default=300, ge=0)

    # Online Evaluation Settings
    eval_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    eval_metrics: list[str] = Field(
        default_factory=lambda: ["faithfulness", "answer_relevancy"]
    )
    eval_timeout_seconds: float = Field(default=120.0, ge=0.0)

    # Unit Token Cost Multipliers (USD per 1,000 tokens)
    llm_cost_per_1k_input_tokens: float = Field(default=0.0015, ge=0.0)
    llm_cost_per_1k_output_tokens: float = Field(default=0.0020, ge=0.0)
    jev_cost_per_1k_input_tokens: float = Field(default=0.0008, ge=0.0)
    jev_cost_per_1k_output_tokens: float = Field(default=0.0008, ge=0.0)

    @field_validator("eval_metrics")
    @classmethod
    def validate_eval_metrics(cls, v: Any) -> list[str]:
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError("eval_metrics must be a list of strings")
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        def patch_source(source: PydanticBaseSettingsSource) -> PydanticBaseSettingsSource:
            orig_decode = getattr(source, "decode_complex_value", None)
            if orig_decode is not None:
                def lenient_decode(field_name: str, field: Any, value: Any) -> Any:
                    try:
                        return orig_decode(field_name, field, value)
                    except Exception:
                        if field_name == "eval_metrics" and isinstance(value, str):
                            return [x.strip() for x in value.split(",") if x.strip()]
                        raise
                source.decode_complex_value = lenient_decode  # type: ignore[method-assign]
            return source

        return (
            init_settings,
            patch_source(env_settings),
            patch_source(dotenv_settings),
            file_secret_settings,
        )
    
    # Logging
    log_level: str = "INFO"
    log_format: Literal["auto", "json", "console"] = "auto"
    log_to_file: bool = True
    log_file_path: str = str(BASE_DIR / "logs" / "app.log")
    log_max_bytes: int = 20_971_520  # 20 MB
    log_backup_count: int = 5
    
    upload_dir: str = str(BASE_DIR / "uploads")
    sample_kb_dir: str = str(BASE_DIR / "data" / "sample_kb")



@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore[call-arg]