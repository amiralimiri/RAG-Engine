"""Pluggable conversation memory subsystem."""

from app.rag.memory.base import (
    ConversationMemoryAdapter,
    ConversationTurn,
    SessionInfo,
)

__all__ = [
    "ConversationMemoryAdapter",
    "ConversationTurn",
    "SessionInfo",
]
