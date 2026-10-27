from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    
    base_url: str = "https://api.gapgpt.app/v1"
    embed_model: str = "text-embedding-3-large"
    llm_model: str = "deepseek-v4-flash"
    
    jev_url: str =  "https://api.gapgpt.app"
    jev_model: str = "typesafe/jev-1.13"
    
    api_key: SecretStr 
    tavily_api_key: SecretStr
    pinecone_api_key: SecretStr
    admin_api_key: SecretStr
    
    pinecone_index_name: str = "industry-agentic-rag-kb"
    pinecone_namespace: str = "company-it-kb"
    
    top_k: int = 4
    max_retries: int = 1

    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = True
    
    # Logging
    log_level: str = "INFO"
    log_format: Literal["auto", "json", "console"] = "auto"
    log_to_file: bool = True
    log_file_path: str = str(BASE_DIR / "logs" / "app.log")
    log_max_bytes: int = 20_971_520  # 20 MB
    log_backup_count: int = 5
    
    audit_db_path: str = str(BASE_DIR / "data" / "audit.db")
    upload_dir: str = str(BASE_DIR / "uploads")
    sample_kb_dir: str = str(BASE_DIR / "data" / "sample_kb")



@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore[call-arg]