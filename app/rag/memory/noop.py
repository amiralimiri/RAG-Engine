"""Stateless pass-through conversation memory adapter."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import BaseMessage

from app.rag.memory.base import ConversationMemoryAdapter, ConversationTurn, SessionInfo


class NoOpMemoryStore(ConversationMemoryAdapter):
    """Stateless no-op conversation memory adapter for disabled-memory deployments."""

    async def aget_messages(
        self,
        session_id: str,
        limit: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[BaseMessage]:
        """Return an empty message list."""
        return []

    async def aget_turns(
        self,
        session_id: str,
        limit: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[ConversationTurn]:
        """Return an empty turn list."""
        return []

    async def aappend_turn(
        self,
        session_id: str,
        user_query: str,
        assistant_answer: str,
        *,
        rewind_turn_index: int | None = None,
        attachments: list[dict[str, Any]] | None = None,
        citations: list[dict[str, Any]] | None = None,
        source_used: str = "direct",
        trace_id: str | None = None,
    ) -> ConversationTurn:
        """Return an ephemeral ConversationTurn model without state persistence."""
        turn_index = rewind_turn_index if rewind_turn_index is not None else 0
        return ConversationTurn(
            session_id=session_id,
            turn_index=turn_index,
            user_query=user_query,
            assistant_answer=assistant_answer,
            attachments=attachments or [],
            citations=citations or [],
            source_used=source_used,
            trace_id=trace_id,
        )

    async def asave_turn(self, turn: ConversationTurn) -> None:
        """Discard turn persistence."""
        return None

    async def aclear(self, session_id: str) -> None:
        """No-op session clear."""
        return None

    async def atruncate_from(self, session_id: str, turn_index: int) -> None:
        """No-op turn truncation."""
        return None

    async def afork_session(
        self,
        source_session_id: str,
        upto_turn_index: int,
        new_session_id: str | None = None,
    ) -> str:
        """Return provided or newly generated session id without state persistence."""
        return new_session_id or uuid.uuid4().hex

    async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]:
        """Return an empty list of sessions."""
        return []

    async def aget_session(self, session_id: str) -> SessionInfo | None:
        """Return None as no sessions are stored."""
        return None
