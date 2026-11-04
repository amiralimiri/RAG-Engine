"""Domain models and protocol seam for conversation memory and session branching."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Canonical domain representation of a completed conversational turn."""

    session_id: str
    turn_index: int = Field(ge=0)
    user_query: str
    assistant_answer: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    source_used: str = Field(default="direct")
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_messages(self) -> list[BaseMessage]:
        """Project the conversational turn into LangChain chat messages for LLM context."""
        human_kwargs: dict[str, Any] = {"attachments": self.attachments} if self.attachments else {}
        return [
            HumanMessage(content=self.user_query, additional_kwargs=human_kwargs),
            AIMessage(content=self.assistant_answer),
        ]


class SessionInfo(BaseModel):
    """Metadata and lineage information for a conversation session."""

    session_id: str
    title: str
    parent_session_id: str | None = None
    forked_from_turn: int | None = None
    created_at: datetime
    updated_at: datetime
    turn_count: int = Field(default=0, ge=0)


class ConversationSession(SessionInfo):
    """Canonical domain aggregate representing a complete conversation session with turns."""

    turns: list[ConversationTurn] = Field(default_factory=list)


@runtime_checkable
class ConversationMemoryAdapter(Protocol):
    """Protocol seam defining conversation memory persistence, resumption, and branching."""

    async def aget_messages(
        self,
        session_id: str,
        limit: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[BaseMessage]:
        """Retrieve bounded LangChain messages for LLM prompting."""
        ...

    async def aget_turns(
        self,
        session_id: str,
        limit: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[ConversationTurn]:
        """Retrieve rich domain turn models for UI hydration and API consumers."""
        ...

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
        """Atomically append a completed conversational turn, handling truncation on rewind."""
        ...

    async def asave_turn(self, turn: ConversationTurn) -> None:
        """Persist a completed conversational turn."""
        ...

    async def aclear(self, session_id: str) -> None:
        """Delete all turns and session metadata for a session."""
        ...

    async def atruncate_from(self, session_id: str, turn_index: int) -> None:
        """Safely rewind a conversation by deleting all turns where turn_index >= k."""
        ...

    async def afork_session(
        self,
        source_session_id: str,
        upto_turn_index: int,
        new_session_id: str | None = None,
    ) -> ConversationSession:
        """Clone turns [0 .. upto_turn_index] into a child session and track lineage."""
        ...

    async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]:
        """Enumerate recent sessions ordered by recency."""
        ...

    async def aget_session(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> ConversationSession | None:
        """Retrieve full conversation session aggregate with chronological turn history."""
        ...


__all__ = [
    "ConversationMemoryAdapter",
    "ConversationSession",
    "ConversationTurn",
    "SessionInfo",
]
