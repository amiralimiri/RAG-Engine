"""Stateless pass-through conversation memory adapter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage

from app.rag.memory.base import (
    ConversationMemoryAdapter,
    ConversationSession,
    ConversationTurn,
    SessionInfo,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.rag.state import Citation


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
        citations: Sequence[Citation] | None = None,
        source_used: str = "direct",
        trace_id: str | None = None,
    ) -> ConversationTurn:
        """Return an ephemeral ConversationTurn model without state persistence."""
        turn_index = rewind_turn_index if rewind_turn_index is not None else 0
        citations_list = list(citations) if citations is not None else []
        return ConversationTurn(
            session_id=session_id,
            turn_index=turn_index,
            user_query=user_query,
            assistant_answer=assistant_answer,
            attachments=attachments or [],
            citations=citations_list,
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
    ) -> ConversationSession:
        """Return an ephemeral ConversationSession without state persistence."""
        target_id = new_session_id or uuid.uuid4().hex
        now = datetime.now(UTC)
        return ConversationSession(
            session_id=target_id,
            title="Ephemeral Fork",
            parent_session_id=source_session_id,
            forked_from_turn=upto_turn_index,
            created_at=now,
            updated_at=now,
            turn_count=0,
            turns=[],
        )

    async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]:
        """Return an empty list of sessions."""
        return []

    async def aget_session(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> ConversationSession | None:
        """Return None as no sessions are stored."""
        return None
