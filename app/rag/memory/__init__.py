"""Pluggable conversation memory subsystem."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.rag.memory.base import (
    ConversationMemoryAdapter,
    ConversationTurn,
    SessionInfo,
)
from app.rag.memory.in_memory import InMemoryConversationStore
from app.rag.memory.noop import NoOpMemoryStore
from app.rag.memory.sqlite import SQLiteConversationStore

if TYPE_CHECKING:
    from app.core.config import Settings

_memory_instance: ConversationMemoryAdapter | None = None
_memory_lock = threading.Lock()


def get_conversation_memory(settings: Settings | None = None) -> ConversationMemoryAdapter:
    """Resolve and return the configured ConversationMemoryAdapter singleton."""
    global _memory_instance

    cfg = settings or get_settings()
    resolved_backend = getattr(cfg, "conversation_memory_backend", "sqlite").lower().strip()

    with _memory_lock:
        if settings is None and _memory_instance is not None:
            return _memory_instance

        adapter: ConversationMemoryAdapter
        if resolved_backend == "in_memory":
            adapter = InMemoryConversationStore()
        elif resolved_backend == "noop":
            adapter = NoOpMemoryStore()
        elif resolved_backend == "sqlite":
            db_path = getattr(cfg, "conversation_db_path", "data/conversations.db")
            adapter = SQLiteConversationStore(db_path=db_path)
        else:
            raise ValueError(f"Unsupported conversation memory backend: '{resolved_backend}'")

        if settings is None:
            _memory_instance = adapter

        return adapter


def reset_conversation_memory() -> None:
    """Reset cached conversation memory singleton for test isolation."""
    global _memory_instance
    with _memory_lock:
        _memory_instance = None


__all__ = [
    "ConversationMemoryAdapter",
    "ConversationTurn",
    "InMemoryConversationStore",
    "NoOpMemoryStore",
    "SQLiteConversationStore",
    "SessionInfo",
    "get_conversation_memory",
    "reset_conversation_memory",
]
