"""Durable SQLite conversation memory persistence adapter with WAL and lineage tracking."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool

from app.rag.memory.base import (
    ConversationMemoryAdapter,
    ConversationSession,
    ConversationTurn,
    SessionInfo,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from langchain_core.messages import BaseMessage


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    parent_session_id TEXT NULL REFERENCES conversation_sessions(session_id) ON DELETE SET NULL,
    forked_from_turn INTEGER NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL,
    user_query TEXT NOT NULL,
    assistant_answer TEXT NOT NULL,
    attachments_json TEXT NULL,
    citations_json TEXT NULL,
    source_used TEXT NOT NULL,
    trace_id TEXT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_turns_session_idx ON conversation_turns(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON conversation_sessions(updated_at DESC);
"""

_TURN_COLUMNS = (
    "session_id, turn_index, user_query, assistant_answer, "
    "attachments_json, citations_json, source_used, trace_id, created_at"
)

_SESSION_SELECT_BASE = """
SELECT
    s.session_id,
    s.title,
    s.parent_session_id,
    s.forked_from_turn,
    s.created_at,
    s.updated_at,
    COUNT(t.id) AS turn_count
FROM conversation_sessions s
LEFT JOIN conversation_turns t ON s.session_id = t.session_id
"""


def _parse_iso_datetime(value: str) -> datetime:
    """Parse ISO datetime string, ensuring UTC timezone awareness."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _row_to_session_info(row: sqlite3.Row) -> SessionInfo:
    """Map a joined database row to a canonical SessionInfo domain model."""
    return SessionInfo(
        session_id=row["session_id"],
        title=row["title"],
        parent_session_id=row["parent_session_id"],
        forked_from_turn=row["forked_from_turn"],
        created_at=_parse_iso_datetime(row["created_at"]),
        updated_at=_parse_iso_datetime(row["updated_at"]),
        turn_count=int(row["turn_count"]),
    )


def _row_to_turn(row: sqlite3.Row) -> ConversationTurn:
    """Map a database row to a canonical ConversationTurn domain model."""
    attachments: list[dict[str, Any]] = (
        json.loads(row["attachments_json"]) if row["attachments_json"] else []
    )
    citations: list[dict[str, Any]] = (
        json.loads(row["citations_json"]) if row["citations_json"] else []
    )
    return ConversationTurn(
        session_id=row["session_id"],
        turn_index=row["turn_index"],
        user_query=row["user_query"],
        assistant_answer=row["assistant_answer"],
        attachments=attachments,
        citations=citations,
        source_used=row["source_used"],
        trace_id=row["trace_id"],
        created_at=_parse_iso_datetime(row["created_at"]),
    )


class SQLiteConversationStore(ConversationMemoryAdapter):
    """Durable SQLite persistence adapter with WAL mode, busy timeout, and lineage tracking."""

    def __init__(self, db_path: str | Path = "data/conversations.db") -> None:
        self._db_path = str(db_path)
        self._write_lock = asyncio.Lock()
        self._init_db()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        """Open a configured SQLite connection and ensure clean closure."""
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize schema, pragmas, and indices."""
        db_path = Path(self._db_path).resolve()
        if db_path.parent:
            os.makedirs(db_path.parent, exist_ok=True)

        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            with conn:
                conn.executescript(SCHEMA_SQL)

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
        """Retrieve rich domain turn models sorted chronologically (turn_index ASC)."""
        if limit is not None and limit <= 0:
            return []

        def _sync_get_turns() -> list[ConversationTurn]:
            with self._connection() as conn:
                params: list[Any] = [session_id]
                before_clause = ""
                if before_turn_index is not None:
                    before_clause = " AND turn_index < ?"
                    params.append(before_turn_index)

                if limit is not None:
                    query = f"""
                    SELECT {_TURN_COLUMNS}
                    FROM (
                        SELECT {_TURN_COLUMNS}
                        FROM conversation_turns
                        WHERE session_id = ?{before_clause}
                        ORDER BY turn_index DESC
                        LIMIT ?
                    )
                    ORDER BY turn_index ASC;
                    """
                    params.append(limit)
                    cursor = conn.execute(query, tuple(params))
                else:
                    query = f"""
                    SELECT {_TURN_COLUMNS}
                    FROM conversation_turns
                    WHERE session_id = ?{before_clause}
                    ORDER BY turn_index ASC;
                    """
                    cursor = conn.execute(query, tuple(params))

                rows = cursor.fetchall()
                return [_row_to_turn(row) for row in rows]

        return await run_in_threadpool(_sync_get_turns)

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
        async with self._write_lock:

            def _sync_append() -> ConversationTurn:
                now_iso = datetime.now(UTC).isoformat()
                title = user_query[:60].strip() or "Untitled Session"
                attachments_clean = attachments or []
                citations_clean = citations or []
                attachments_json = json.dumps(attachments_clean) if attachments_clean else None
                citations_json = json.dumps(citations_clean) if citations_clean else None

                with self._connection() as conn:
                    with conn:
                        if rewind_turn_index is not None:
                            conn.execute(
                                "DELETE FROM conversation_turns WHERE session_id = ? AND turn_index >= ?;",
                                (session_id, rewind_turn_index),
                            )
                            turn_index = rewind_turn_index
                        else:
                            cursor = conn.execute(
                                "SELECT COALESCE(MAX(turn_index) + 1, 0) AS next_idx FROM conversation_turns WHERE session_id = ?;",
                                (session_id,),
                            )
                            turn_index = int(cursor.fetchone()["next_idx"])

                        conn.execute(
                            """
                            INSERT INTO conversation_sessions (session_id, title, created_at, updated_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at;
                            """,
                            (session_id, title, now_iso, now_iso),
                        )

                        conn.execute(
                            """
                            INSERT INTO conversation_turns (
                                session_id, turn_index, user_query, assistant_answer,
                                attachments_json, citations_json, source_used, trace_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                session_id,
                                turn_index,
                                user_query,
                                assistant_answer,
                                attachments_json,
                                citations_json,
                                source_used,
                                trace_id,
                                now_iso,
                            ),
                        )

                        return ConversationTurn(
                            session_id=session_id,
                            turn_index=turn_index,
                            user_query=user_query,
                            assistant_answer=assistant_answer,
                            attachments=attachments_clean,
                            citations=citations_clean,
                            source_used=source_used,
                            trace_id=trace_id,
                            created_at=_parse_iso_datetime(now_iso),
                        )

            return await run_in_threadpool(_sync_append)

    async def asave_turn(self, turn: ConversationTurn) -> None:
        """Persist or upsert a completed conversational turn."""
        async with self._write_lock:

            def _sync_save() -> None:
                now_iso = datetime.now(UTC).isoformat()
                created_iso = turn.created_at.isoformat()
                title = turn.user_query[:60].strip() or "Untitled Session"
                attachments_json = json.dumps(turn.attachments) if turn.attachments else None
                citations_json = json.dumps(turn.citations) if turn.citations else None

                with self._connection() as conn:
                    with conn:
                        # 1. Upsert parent session record
                        conn.execute(
                            """
                            INSERT INTO conversation_sessions (session_id, title, created_at, updated_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at;
                            """,
                            (turn.session_id, title, created_iso, now_iso),
                        )

                        # 2. Insert or replace turn
                        conn.execute(
                            """
                            INSERT INTO conversation_turns (
                                session_id, turn_index, user_query, assistant_answer,
                                attachments_json, citations_json, source_used, trace_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(session_id, turn_index) DO UPDATE SET
                                user_query = excluded.user_query,
                                assistant_answer = excluded.assistant_answer,
                                attachments_json = excluded.attachments_json,
                                citations_json = excluded.citations_json,
                                source_used = excluded.source_used,
                                trace_id = excluded.trace_id,
                                created_at = excluded.created_at;
                            """,
                            (
                                turn.session_id,
                                turn.turn_index,
                                turn.user_query,
                                turn.assistant_answer,
                                attachments_json,
                                citations_json,
                                turn.source_used,
                                turn.trace_id,
                                created_iso,
                            ),
                        )

            await run_in_threadpool(_sync_save)

    async def aclear(self, session_id: str) -> None:
        """Delete all turns and session metadata for a session (cascades via foreign key)."""
        async with self._write_lock:

            def _sync_clear() -> None:
                with self._connection() as conn:
                    with conn:
                        conn.execute(
                            "DELETE FROM conversation_sessions WHERE session_id = ?;",
                            (session_id,),
                        )

            await run_in_threadpool(_sync_clear)

    async def atruncate_from(self, session_id: str, turn_index: int) -> None:
        """Safely rewind a conversation by deleting all turns where turn_index >= k."""
        async with self._write_lock:

            def _sync_truncate() -> None:
                now_iso = datetime.now(UTC).isoformat()
                with self._connection() as conn:
                    with conn:
                        conn.execute(
                            "DELETE FROM conversation_turns WHERE session_id = ? AND turn_index >= ?;",
                            (session_id, turn_index),
                        )
                        conn.execute(
                            "UPDATE conversation_sessions SET updated_at = ? WHERE session_id = ?;",
                            (now_iso, session_id),
                        )

            await run_in_threadpool(_sync_truncate)

    async def afork_session(
        self,
        source_session_id: str,
        upto_turn_index: int,
        new_session_id: str | None = None,
    ) -> ConversationSession:
        """Clone turns [0 .. upto_turn_index] into a child session and return the complete aggregate."""
        if upto_turn_index < -1:
            raise ValueError(f"Invalid upto_turn_index: {upto_turn_index}. Must be >= -1.")

        async with self._write_lock:
            target_id = new_session_id or uuid.uuid4().hex

            def _sync_fork() -> ConversationSession:
                now_iso = datetime.now(UTC).isoformat()
                with self._connection() as conn:
                    with conn:
                        cursor = conn.execute(
                            "SELECT title FROM conversation_sessions WHERE session_id = ?;",
                            (source_session_id,),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise KeyError(f"Session not found: {source_session_id}")

                        source_title = row["title"] if row["title"] else source_session_id
                        title = f"{source_title} (Fork)"

                        conn.execute(
                            """
                            INSERT INTO conversation_sessions (
                                session_id, title, parent_session_id, forked_from_turn, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?);
                            """,
                            (target_id, title, source_session_id, upto_turn_index, now_iso, now_iso),
                        )

                        cloned_turns: list[ConversationTurn] = []
                        if upto_turn_index >= 0:
                            conn.execute(
                                f"""
                                INSERT INTO conversation_turns (
                                    {_TURN_COLUMNS}
                                )
                                SELECT ?, turn_index, user_query, assistant_answer,
                                       attachments_json, citations_json, source_used, trace_id, created_at
                                FROM conversation_turns
                                WHERE session_id = ? AND turn_index <= ?
                                ORDER BY turn_index ASC;
                                """,
                                (target_id, source_session_id, upto_turn_index),
                            )

                            turn_cursor = conn.execute(
                                f"""
                                SELECT {_TURN_COLUMNS}
                                FROM conversation_turns
                                WHERE session_id = ?
                                ORDER BY turn_index ASC;
                                """,
                                (target_id,),
                            )
                            cloned_turns = [_row_to_turn(r) for r in turn_cursor.fetchall()]

                        now_dt = _parse_iso_datetime(now_iso)
                        return ConversationSession(
                            session_id=target_id,
                            title=title,
                            parent_session_id=source_session_id,
                            forked_from_turn=upto_turn_index,
                            created_at=now_dt,
                            updated_at=now_dt,
                            turn_count=len(cloned_turns),
                            turns=cloned_turns,
                        )

            return await run_in_threadpool(_sync_fork)

    async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]:
        """Enumerate recent sessions ordered by recency."""

        def _sync_list() -> list[SessionInfo]:
            with self._connection() as conn:
                query = f"""
                {_SESSION_SELECT_BASE}
                GROUP BY s.session_id
                ORDER BY s.updated_at DESC
                LIMIT ?;
                """
                cursor = conn.execute(query, (limit,))
                rows = cursor.fetchall()
                return [_row_to_session_info(row) for row in rows]

        return await run_in_threadpool(_sync_list)

    async def aget_session(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> ConversationSession | None:
        """Retrieve full conversation session aggregate with chronological turn history."""

        def _sync_get_session() -> ConversationSession | None:
            with self._connection() as conn:
                cursor = conn.execute(
                    f"""
                    {_SESSION_SELECT_BASE}
                    WHERE s.session_id = ?
                    GROUP BY s.session_id;
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
                if not row or row["session_id"] is None:
                    return None

                info = _row_to_session_info(row)

                if limit is not None:
                    if limit <= 0:
                        turns: list[ConversationTurn] = []
                    else:
                        turn_cursor = conn.execute(
                            f"""
                            SELECT {_TURN_COLUMNS}
                            FROM conversation_turns
                            WHERE session_id = ?
                            ORDER BY turn_index DESC
                            LIMIT ?;
                            """,
                            (session_id, limit),
                        )
                        turn_rows = turn_cursor.fetchall()
                        turns = [_row_to_turn(r) for r in reversed(turn_rows)]
                else:
                    turn_cursor = conn.execute(
                        f"""
                        SELECT {_TURN_COLUMNS}
                        FROM conversation_turns
                        WHERE session_id = ?
                        ORDER BY turn_index ASC;
                        """,
                        (session_id,),
                    )
                    turn_rows = turn_cursor.fetchall()
                    turns = [_row_to_turn(r) for r in turn_rows]

                return ConversationSession(
                    **info.model_dump(),
                    turns=turns,
                )

        return await run_in_threadpool(_sync_get_session)
