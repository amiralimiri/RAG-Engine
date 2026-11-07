"""Prompt management subsystem conforming to unified PromptStore protocol seam."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.rag.prompts.base import (
    CANONICAL_PROMPTS,
    PromptStore,
    RenderedPrompt,
    interpolate_mustache,
)
from app.rag.prompts.formatting import format_chat_history
from app.rag.prompts.langfuse import LangfusePromptStore
from app.rag.prompts.local import LocalPromptStore

if TYPE_CHECKING:
    from app.core.config import Settings

_prompt_store_instance: PromptStore | None = None
_prompt_store_lock = threading.Lock()


def get_prompt_store(
    settings: Settings | None = None,
    store_type: str | None = None,
    store: PromptStore | None = None,
) -> PromptStore:
    """Resolve and return the configured PromptStore singleton or customized instance."""
    global _prompt_store_instance

    if store is not None:
        with _prompt_store_lock:
            _prompt_store_instance = store
            return _prompt_store_instance

    with _prompt_store_lock:
        if settings is None and store_type is None and _prompt_store_instance is not None:
            return _prompt_store_instance

        cfg = settings or get_settings()
        resolved_type = (store_type or "langfuse").lower().strip()

        adapter: PromptStore
        if resolved_type == "langfuse":
            adapter = LangfusePromptStore(settings=cfg)
        elif resolved_type == "local":
            adapter = LocalPromptStore()
        else:
            raise ValueError(f"Unsupported prompt store type: '{resolved_type}'")

        if settings is None and store_type is None:
            _prompt_store_instance = adapter

        return adapter


def reset_prompt_store() -> None:
    """Reset cached PromptStore singleton for test isolation."""
    global _prompt_store_instance
    with _prompt_store_lock:
        _prompt_store_instance = None


__all__ = [
    "CANONICAL_PROMPTS",
    "LangfusePromptStore",
    "LocalPromptStore",
    "PromptStore",
    "RenderedPrompt",
    "format_chat_history",
    "get_prompt_store",
    "interpolate_mustache",
    "reset_prompt_store",
]
