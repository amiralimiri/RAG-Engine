"""Thread-safe in-memory conversation memory adapter."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import BaseMessage

from app.rag.memory.base import ConversationMemoryAdapter, ConversationTurn, SessionInfo


class InMemoryConversationStore(ConversationMemoryAdapter):
    """In-memory, thread-safe implementation of ConversationMemoryAdapter for fast unit testing."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionInfo] = {}
        self._turns: dict[str, list[ConversationTurn]] = {}

    async def aget_messages(
        self,
        session_id: str,
        limit: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[BaseMessage]:
        """Retrieve bounded LangChain messages for LLM prompting."""
        turns = await self.aget_turns(session_id, limit=limit, before_turn_index=before_turn_index)
        messages: list[BaseMessage] = []
        for turn in turns:
            messages.extend(turn.to_messages())
        return messages

    async def aget_turns(
        self,
        session_id: str,
        limit: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[ConversationTurn]:
        """Retrieve rich domain turn models sorted chronologically."""
        with self._lock:
            turns = self._turns.get(session_id, [])
            if before_turn_index is not None:
                turns = [t for t in turns if t.turn_index < before_turn_index]
            if limit is not None:
                selected = turns[-limit:] if limit > 0 else []
            else:
                selected = list(turns)
            return [t.model_copy(deep=True) for t in selected]

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
        with self._lock:
            now = datetime.now(UTC)
            turns = self._turns.setdefault(session_id, [])

            if rewind_turn_index is not None:
                turns = [t for t in turns if t.turn_index < rewind_turn_index]
                self._turns[session_id] = turns
                turn_index = rewind_turn_index
            else:
                turn_index = max([t.turn_index for t in turns], default=-1) + 1

            turn = ConversationTurn(
                session_id=session_id,
                turn_index=turn_index,
                user_query=user_query,
                assistant_answer=assistant_answer,
                attachments=attachments or [],
                citations=citations or [],
                source_used=source_used,
                trace_id=trace_id,
                created_at=now,
            )

            turns.append(turn)
            turns.sort(key=lambda t: t.turn_index)

            if session_id not in self._sessions:
                title = user_query[:60].strip() or "Untitled Session"
                self._sessions[session_id] = SessionInfo(
                    session_id=session_id,
                    title=title,
                    created_at=now,
                    updated_at=now,
                    turn_count=len(turns),
                )
            else:
                self._sessions[session_id] = self._sessions[session_id].model_copy(
                    update={
                        "turn_count": len(turns),
                        "updated_at": now,
                    }
                )

            return turn.model_copy(deep=True)

    async def asave_turn(self, turn: ConversationTurn) -> None:
        """Persist or upsert a completed conversational turn."""
        with self._lock:
            session_id = turn.session_id
            now = datetime.now(UTC)

            if session_id not in self._sessions:
                title = turn.user_query[:60].strip() or "Untitled Session"
                self._sessions[session_id] = SessionInfo(
                    session_id=session_id,
                    title=title,
                    created_at=turn.created_at,
                    updated_at=now,
                    turn_count=1,
                )
            else:
                existing_session = self._sessions[session_id]
                self._sessions[session_id] = existing_session.model_copy(update={"updated_at": now})

            turns = self._turns.setdefault(session_id, [])
            replaced = False
            for idx, existing_turn in enumerate(turns):
                if existing_turn.turn_index == turn.turn_index:
                    turns[idx] = turn
                    replaced = True
                    break

            if not replaced:
                turns.append(turn)
                turns.sort(key=lambda t: t.turn_index)

            self._sessions[session_id] = self._sessions[session_id].model_copy(update={"turn_count": len(turns)})

    async def aclear(self, session_id: str) -> None:
        """Delete all turns and session metadata for a session."""
        with self._lock:
            self._sessions.pop(session_id, None)
            self._turns.pop(session_id, None)

    async def atruncate_from(self, session_id: str, turn_index: int) -> None:
        """Safely rewind a conversation by deleting all turns where turn_index >= k."""
        with self._lock:
            if session_id not in self._turns:
                return
            self._turns[session_id] = [turn for turn in self._turns[session_id] if turn.turn_index < turn_index]
            if session_id in self._sessions:
                self._sessions[session_id] = self._sessions[session_id].model_copy(
                    update={
                        "turn_count": len(self._turns[session_id]),
                        "updated_at": datetime.now(UTC),
                    }
                )

    async def afork_session(
        self,
        source_session_id: str,
        upto_turn_index: int,
        new_session_id: str | None = None,
    ) -> str:
        """Clone turns [0 .. upto_turn_index] into a child session and track lineage."""
        with self._lock:
            target_id = new_session_id or uuid.uuid4().hex
            now = datetime.now(UTC)

            parent_session = self._sessions.get(source_session_id)
            if parent_session and parent_session.title:
                title = f"{parent_session.title} (Fork)"
            else:
                title = f"Fork of {source_session_id}"

            source_turns = self._turns.get(source_session_id, [])
            cloned_turns: list[ConversationTurn] = []
            if upto_turn_index >= 0:
                for turn in source_turns:
                    if turn.turn_index <= upto_turn_index:
                        cloned_turns.append(
                            turn.model_copy(
                                update={"session_id": target_id},
                                deep=True,
                            )
                        )

            self._turns[target_id] = cloned_turns
            self._sessions[target_id] = SessionInfo(
                session_id=target_id,
                title=title,
                parent_session_id=source_session_id,
                forked_from_turn=upto_turn_index,
                created_at=now,
                updated_at=now,
                turn_count=len(cloned_turns),
            )

            return target_id

    async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]:
        """Enumerate recent sessions ordered by recency."""
        with self._lock:
            sorted_sessions = sorted(
                self._sessions.values(),
                key=lambda s: s.updated_at,
                reverse=True,
            )
            return [s.model_copy(deep=True) for s in sorted_sessions[:limit]]

    async def aget_session(self, session_id: str) -> SessionInfo | None:
        """Retrieve session metadata by session ID."""
        with self._lock:
            session = self._sessions.get(session_id)
            return session.model_copy(deep=True) if session else None
