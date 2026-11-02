"""Tests for durable SQLite conversation memory persistence adapter with WAL and lineage tracking."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.rag.memory import (
    ConversationMemoryAdapter,
    ConversationTurn,
    get_conversation_memory,
    reset_conversation_memory,
)
from app.rag.memory.sqlite import SQLiteConversationStore


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SQLiteConversationStore:
    """Fixture providing a clean SQLite store backed by a temporary file."""
    db_file = tmp_path / "test_conversations.db"
    return SQLiteConversationStore(db_path=db_file)


@pytest.mark.asyncio
async def test_sqlite_protocol_compliance(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that SQLiteConversationStore conforms to ConversationMemoryAdapter protocol."""
    assert isinstance(sqlite_store, ConversationMemoryAdapter)


@pytest.mark.asyncio
async def test_sqlite_persistence_across_connections(tmp_path: Path) -> None:
    """Persist a turn, drop the store instance, re-instantiate against the same DB file, and verify persistence."""
    db_file = tmp_path / "test_persistence.db"
    store_1 = SQLiteConversationStore(db_path=db_file)

    turn = ConversationTurn(
        session_id="session-persist-1",
        turn_index=0,
        user_query="What is WAL mode?",
        assistant_answer="Write-Ahead Logging provides concurrency and durability.",
        attachments=[{"type": "image", "url": "https://example.com/wal.png"}],
        citations=[{"source": "sqlite_docs.md", "page": 42}],
        source_used="vectorstore",
        trace_id="trace-persist-001",
    )
    await store_1.asave_turn(turn)

    # Re-instantiate against the same DB file to simulate server restart
    store_2 = SQLiteConversationStore(db_path=db_file)

    session = await store_2.aget_session("session-persist-1")
    assert session is not None
    assert session.session_id == "session-persist-1"
    assert session.title == "What is WAL mode?"
    assert session.turn_count == 1

    turns = await store_2.aget_turns("session-persist-1")
    assert len(turns) == 1
    assert turns[0].session_id == "session-persist-1"
    assert turns[0].turn_index == 0
    assert turns[0].user_query == "What is WAL mode?"
    assert turns[0].assistant_answer == "Write-Ahead Logging provides concurrency and durability."
    assert turns[0].attachments == [{"type": "image", "url": "https://example.com/wal.png"}]
    assert turns[0].citations == [{"source": "sqlite_docs.md", "page": 42}]
    assert turns[0].source_used == "vectorstore"
    assert turns[0].trace_id == "trace-persist-001"


@pytest.mark.asyncio
async def test_sqlite_save_and_get_turns_and_messages(sqlite_store: SQLiteConversationStore) -> None:
    """Validate turn persistence, auto-session creation, and message projection with JSON deserialization."""
    turn_0 = ConversationTurn(
        session_id="session-json-test",
        turn_index=0,
        user_query="Tell me about python sqlite3",
        assistant_answer="Python includes sqlite3 in the standard library.",
        citations=[{"file": "sqlite.txt"}],
        source_used="vectorstore",
    )
    turn_1 = ConversationTurn(
        session_id="session-json-test",
        turn_index=1,
        user_query="Does it support WAL?",
        assistant_answer="Yes, via PRAGMA journal_mode=WAL.",
    )

    # Save out of order to ensure chronological ordering invariant (turn_index ASC)
    await sqlite_store.asave_turn(turn_1)
    await sqlite_store.asave_turn(turn_0)

    turns = await sqlite_store.aget_turns("session-json-test")
    assert len(turns) == 2
    assert turns[0].turn_index == 0
    assert turns[0].user_query == "Tell me about python sqlite3"
    assert turns[0].citations == [{"file": "sqlite.txt"}]
    assert turns[1].turn_index == 1
    assert turns[1].user_query == "Does it support WAL?"

    messages = await sqlite_store.aget_messages("session-json-test")
    assert len(messages) == 4
    assert messages[0].content == "Tell me about python sqlite3"
    assert messages[1].content == "Python includes sqlite3 in the standard library."
    assert messages[2].content == "Does it support WAL?"
    assert messages[3].content == "Yes, via PRAGMA journal_mode=WAL."

    session = await sqlite_store.aget_session("session-json-test")
    assert session is not None
    assert session.turn_count == 2
    assert session.title == "Does it support WAL?"


@pytest.mark.asyncio
async def test_sqlite_bounded_turns_retrieval(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that limit returns the latest limit turns/messages in ascending order."""
    for idx in range(5):
        await sqlite_store.asave_turn(
            ConversationTurn(
                session_id="session-bounded",
                turn_index=idx,
                user_query=f"Question {idx}",
                assistant_answer=f"Answer {idx}",
            )
        )

    # All turns
    all_turns = await sqlite_store.aget_turns("session-bounded")
    assert len(all_turns) == 5

    # Bounded turns (limit=2 => turns 3 and 4 in ascending order)
    bounded_turns = await sqlite_store.aget_turns("session-bounded", limit=2)
    assert len(bounded_turns) == 2
    assert bounded_turns[0].turn_index == 3
    assert bounded_turns[1].turn_index == 4

    # Bounded messages
    bounded_messages = await sqlite_store.aget_messages("session-bounded", limit=2)
    assert len(bounded_messages) == 4
    assert bounded_messages[0].content == "Question 3"
    assert bounded_messages[1].content == "Answer 3"
    assert bounded_messages[2].content == "Question 4"
    assert bounded_messages[3].content == "Answer 4"

    # Non-existent session returns empty lists
    assert await sqlite_store.aget_turns("non-existent") == []
    assert await sqlite_store.aget_messages("non-existent") == []

    # limit=0 returns empty list
    assert await sqlite_store.aget_turns("session-bounded", limit=0) == []
    assert await sqlite_store.aget_messages("session-bounded", limit=0) == []


@pytest.mark.asyncio
async def test_sqlite_upsert_turn(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that saving a turn with existing turn_index replaces it."""
    turn_initial = ConversationTurn(
        session_id="session-upsert",
        turn_index=0,
        user_query="Initial query",
        assistant_answer="Initial answer",
    )
    await sqlite_store.asave_turn(turn_initial)

    turn_updated = ConversationTurn(
        session_id="session-upsert",
        turn_index=0,
        user_query="Initial query",
        assistant_answer="Updated answer",
    )
    await sqlite_store.asave_turn(turn_updated)

    turns = await sqlite_store.aget_turns("session-upsert")
    assert len(turns) == 1
    assert turns[0].assistant_answer == "Updated answer"

    session = await sqlite_store.aget_session("session-upsert")
    assert session is not None
    assert session.turn_count == 1


@pytest.mark.asyncio
async def test_sqlite_truncate_from(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that atruncate_from deletes turns where turn_index >= k and updates turn_count."""
    for idx in range(4):
        await sqlite_store.asave_turn(
            ConversationTurn(
                session_id="session-trunc",
                turn_index=idx,
                user_query=f"Query {idx}",
                assistant_answer=f"Answer {idx}",
            )
        )

    session_before = await sqlite_store.aget_session("session-trunc")
    assert session_before is not None
    assert session_before.turn_count == 4

    # Truncate from turn 2
    await sqlite_store.atruncate_from("session-trunc", turn_index=2)

    remaining_turns = await sqlite_store.aget_turns("session-trunc")
    assert len(remaining_turns) == 2
    assert [t.turn_index for t in remaining_turns] == [0, 1]

    session_after = await sqlite_store.aget_session("session-trunc")
    assert session_after is not None
    assert session_after.turn_count == 2


@pytest.mark.asyncio
async def test_sqlite_cascade_delete(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that aclear deletes the session record and cascades to all child turns via foreign key."""
    await sqlite_store.asave_turn(
        ConversationTurn(
            session_id="session-cascade",
            turn_index=0,
            user_query="Query to clear",
            assistant_answer="Answer to clear",
        )
    )

    assert await sqlite_store.aget_session("session-cascade") is not None
    assert len(await sqlite_store.aget_turns("session-cascade")) == 1

    await sqlite_store.aclear("session-cascade")

    assert await sqlite_store.aget_session("session-cascade") is None
    assert await sqlite_store.aget_turns("session-cascade") == []
    assert await sqlite_store.aget_messages("session-cascade") == []

    # Safe to call on non-existent session
    await sqlite_store.aclear("session-cascade")


@pytest.mark.asyncio
async def test_sqlite_fork_session(sqlite_store: SQLiteConversationStore) -> None:
    """Validate conversation branching with turn cloning and lineage tracking."""
    for idx in range(4):
        await sqlite_store.asave_turn(
            ConversationTurn(
                session_id="session-parent",
                turn_index=idx,
                user_query=f"Parent query {idx}",
                assistant_answer=f"Parent answer {idx}",
            )
        )

    # Fork at turn 1 (copying turns 0 and 1)
    fork_id = await sqlite_store.afork_session("session-parent", upto_turn_index=1)
    assert fork_id != "session-parent"

    # Parent remains unaffected
    parent_turns = await sqlite_store.aget_turns("session-parent")
    assert len(parent_turns) == 4

    # Child has turns 0 and 1 re-bound to fork_id
    child_turns = await sqlite_store.aget_turns(fork_id)
    assert len(child_turns) == 2
    assert [t.turn_index for t in child_turns] == [0, 1]
    assert all(t.session_id == fork_id for t in child_turns)
    assert child_turns[0].user_query == "Parent query 0"
    assert child_turns[1].user_query == "Parent query 1"

    # Verify session lineage
    child_session = await sqlite_store.aget_session(fork_id)
    assert child_session is not None
    assert child_session.parent_session_id == "session-parent"
    assert child_session.forked_from_turn == 1
    assert child_session.turn_count == 2
    assert "(Fork)" in child_session.title


@pytest.mark.asyncio
async def test_sqlite_fork_turn_zero(sqlite_store: SQLiteConversationStore) -> None:
    """Validate turn 0 branching (upto_turn_index = -1) creating empty child session with parent lineage."""
    await sqlite_store.asave_turn(
        ConversationTurn(
            session_id="session-fork-zero",
            turn_index=0,
            user_query="Origin query",
            assistant_answer="Origin answer",
        )
    )

    child_id = await sqlite_store.afork_session(
        "session-fork-zero",
        upto_turn_index=-1,
        new_session_id="session-fork-zero-child",
    )
    assert child_id == "session-fork-zero-child"

    # Child has 0 turns
    child_turns = await sqlite_store.aget_turns(child_id)
    assert child_turns == []

    # Lineage is preserved
    child_session = await sqlite_store.aget_session(child_id)
    assert child_session is not None
    assert child_session.session_id == "session-fork-zero-child"
    assert child_session.parent_session_id == "session-fork-zero"
    assert child_session.forked_from_turn == -1
    assert child_session.turn_count == 0


@pytest.mark.asyncio
async def test_sqlite_fork_nonexistent_parent_raises_integrity_error(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that attempting to fork from a non-existent parent enforces foreign key integrity."""
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        await sqlite_store.afork_session("nonexistent-parent", upto_turn_index=0)


@pytest.mark.asyncio
async def test_sqlite_nonexistent_session_queries(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that queries on non-existent sessions return safe default values."""
    assert await sqlite_store.aget_session("missing-session") is None
    assert await sqlite_store.aget_turns("missing-session") == []
    assert await sqlite_store.aget_messages("missing-session") == []
    # Safe to call mutation methods on non-existent session
    await sqlite_store.atruncate_from("missing-session", turn_index=0)
    await sqlite_store.aclear("missing-session")


@pytest.mark.asyncio
async def test_sqlite_list_sessions(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that alist_sessions returns sessions ordered by updated_at descending with correct turn_count."""
    base_time = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    turn_old = ConversationTurn(
        session_id="session-old",
        turn_index=0,
        user_query="Old question",
        assistant_answer="Old answer",
        created_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
    )
    turn_mid = ConversationTurn(
        session_id="session-mid",
        turn_index=0,
        user_query="Mid question",
        assistant_answer="Mid answer",
        created_at=datetime(2026, 11, 1, 11, 0, tzinfo=UTC),
    )
    turn_new = ConversationTurn(
        session_id="session-new",
        turn_index=0,
        user_query="New question",
        assistant_answer="New answer",
        created_at=base_time,
    )

    await sqlite_store.asave_turn(turn_old)
    await sqlite_store.asave_turn(turn_mid)
    await sqlite_store.asave_turn(turn_new)

    # List all
    sessions = await sqlite_store.alist_sessions(limit=10)
    assert len(sessions) == 3
    assert [s.session_id for s in sessions] == ["session-new", "session-mid", "session-old"]
    assert all(s.turn_count == 1 for s in sessions)

    # Bounded list
    bounded = await sqlite_store.alist_sessions(limit=2)
    assert len(bounded) == 2
    assert [s.session_id for s in bounded] == ["session-new", "session-mid"]


@pytest.mark.asyncio
async def test_sqlite_concurrent_access(sqlite_store: SQLiteConversationStore) -> None:
    """Validate thread-safe concurrent saves and retrievals across multiple sessions."""

    async def worker(worker_id: int) -> None:
        session_id = f"session-worker-{worker_id}"
        for turn_idx in range(5):
            turn = ConversationTurn(
                session_id=session_id,
                turn_index=turn_idx,
                user_query=f"Worker {worker_id} query {turn_idx}",
                assistant_answer=f"Worker {worker_id} answer {turn_idx}",
            )
            await sqlite_store.asave_turn(turn)
            messages = await sqlite_store.aget_messages(session_id, limit=2)
            assert len(messages) <= 4

    await asyncio.gather(*(worker(i) for i in range(5)))

    sessions = await sqlite_store.alist_sessions(limit=50)
    assert len(sessions) == 5
    for session in sessions:
        assert session.turn_count == 5
        turns = await sqlite_store.aget_turns(session.session_id)
        assert len(turns) == 5


@pytest.mark.asyncio
async def test_sqlite_factory_resolution(tmp_path: Path) -> None:
    """Validate factory resolution when conversation_memory_backend == 'sqlite' and end-to-end operation."""
    db_file = tmp_path / "factory_test.db"
    settings = Settings(
        llm_api_key=SecretStr("mock"),
        embed_api_key=SecretStr("mock"),
        jev_api_key=SecretStr("mock"),
        tavily_api_key=SecretStr("mock"),
        pinecone_api_key=SecretStr("mock"),
        admin_api_key=SecretStr("mock"),
        conversation_memory_backend="sqlite",
        conversation_db_path=str(db_file),
    )

    reset_conversation_memory()
    mem = get_conversation_memory(settings=settings)
    assert isinstance(mem, SQLiteConversationStore)

    # Verify adapter operates on the configured path
    turn = ConversationTurn(
        session_id="factory-session",
        turn_index=0,
        user_query="Testing factory adapter",
        assistant_answer="Factory works properly.",
    )
    await mem.asave_turn(turn)
    loaded = await mem.aget_turns("factory-session")
    assert len(loaded) == 1
    assert loaded[0].user_query == "Testing factory adapter"
    assert db_file.exists()


@pytest.mark.asyncio
async def test_sqlite_append_turn_monotonic_indexing(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that sequential aappend_turn calls assign monotonic indices (0, 1, 2)."""
    session_id = "session-append-monotonic"
    turn_0 = await sqlite_store.aappend_turn(
        session_id,
        "Query 0",
        "Answer 0",
        attachments=[{"type": "doc", "id": "1"}],
        citations=[{"source": "test.txt"}],
        source_used="vectorstore",
        trace_id="tr-0",
    )
    turn_1 = await sqlite_store.aappend_turn(session_id, "Query 1", "Answer 1")
    turn_2 = await sqlite_store.aappend_turn(session_id, "Query 2", "Answer 2")

    assert turn_0.turn_index == 0
    assert turn_0.user_query == "Query 0"
    assert turn_0.attachments == [{"type": "doc", "id": "1"}]
    assert turn_0.citations == [{"source": "test.txt"}]
    assert turn_0.source_used == "vectorstore"
    assert turn_0.trace_id == "tr-0"
    assert turn_1.turn_index == 1
    assert turn_2.turn_index == 2

    turns = await sqlite_store.aget_turns(session_id)
    assert len(turns) == 3
    assert [t.turn_index for t in turns] == [0, 1, 2]

    session = await sqlite_store.aget_session(session_id)
    assert session is not None
    assert session.turn_count == 3
    assert session.title == "Query 0"


@pytest.mark.asyncio
async def test_sqlite_append_turn_rewind_truncation(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that aappend_turn with rewind_turn_index atomically prunes subsequent turns and saves at rewind index."""
    session_id = "session-rewind-append"
    await sqlite_store.aappend_turn(session_id, "Q0", "A0")
    await sqlite_store.aappend_turn(session_id, "Q1", "A1")
    await sqlite_store.aappend_turn(session_id, "Q2", "A2")

    turns = await sqlite_store.aget_turns(session_id)
    assert len(turns) == 3

    rewound_turn = await sqlite_store.aappend_turn(
        session_id,
        "Q1 Replacement",
        "A1 Replacement",
        rewind_turn_index=1,
    )
    assert rewound_turn.turn_index == 1
    assert rewound_turn.user_query == "Q1 Replacement"

    turns_after = await sqlite_store.aget_turns(session_id)
    assert len(turns_after) == 2
    assert [t.turn_index for t in turns_after] == [0, 1]
    assert turns_after[1].user_query == "Q1 Replacement"
    assert turns_after[1].assistant_answer == "A1 Replacement"

    session = await sqlite_store.aget_session(session_id)
    assert session is not None
    assert session.turn_count == 2


@pytest.mark.asyncio
async def test_sqlite_bounded_queries_with_before_turn_index(sqlite_store: SQLiteConversationStore) -> None:
    """Validate that aget_turns and aget_messages filter turn_index < before_turn_index with limit."""
    session_id = "session-bounded-before"
    for i in range(4):
        await sqlite_store.aappend_turn(session_id, f"Query {i}", f"Answer {i}")

    # before_turn_index=2 and limit=1 should return only turn index 1
    turns = await sqlite_store.aget_turns(session_id, limit=1, before_turn_index=2)
    assert len(turns) == 1
    assert turns[0].turn_index == 1
    assert turns[0].user_query == "Query 1"

    messages = await sqlite_store.aget_messages(session_id, limit=1, before_turn_index=2)
    assert len(messages) == 2
    assert messages[0].content == "Query 1"
    assert messages[1].content == "Answer 1"

    # before_turn_index=2 with limit=None returns turns 0 and 1
    turns_all_before = await sqlite_store.aget_turns(session_id, before_turn_index=2)
    assert len(turns_all_before) == 2
    assert [t.turn_index for t in turns_all_before] == [0, 1]


@pytest.mark.asyncio
async def test_sqlite_concurrent_append_turn(sqlite_store: SQLiteConversationStore) -> None:
    """Validate thread-safe concurrent appends to the same session without turn_index collision."""
    session_id = "session-concurrent-append"
    num_concurrent = 10

    async def append_worker(idx: int) -> ConversationTurn:
        return await sqlite_store.aappend_turn(
            session_id,
            f"Concurrent query {idx}",
            f"Concurrent answer {idx}",
        )

    results = await asyncio.gather(*(append_worker(i) for i in range(num_concurrent)))
    indices = sorted([turn.turn_index for turn in results])
    assert indices == list(range(num_concurrent))

    turns = await sqlite_store.aget_turns(session_id)
    assert len(turns) == num_concurrent
    assert [t.turn_index for t in turns] == list(range(num_concurrent))

    session = await sqlite_store.aget_session(session_id)
    assert session is not None
    assert session.turn_count == num_concurrent


@pytest.mark.asyncio
async def test_sqlite_append_turn_rollback_on_failure(sqlite_store: SQLiteConversationStore) -> None:
    """Validate atomic rollback: if insert fails during rewind append, previous turns are not permanently lost."""
    session_id = "session-rollback"
    await sqlite_store.aappend_turn(session_id, "Q0", "A0")
    await sqlite_store.aappend_turn(session_id, "Q1", "A1")
    await sqlite_store.aappend_turn(session_id, "Q2", "A2")

    turns = await sqlite_store.aget_turns(session_id)
    assert len(turns) == 3

    # Install an abort trigger to simulate a write failure specifically during turn insertion
    with sqlite_store._connection() as conn:
        with conn:
            conn.execute(
                """
                CREATE TRIGGER fail_turn_insert BEFORE INSERT ON conversation_turns
                WHEN NEW.user_query = 'Simulated Fail'
                BEGIN
                    SELECT RAISE(ABORT, 'Simulated disk error during insert');
                END;
                """
            )

    with pytest.raises(sqlite3.DatabaseError, match="Simulated disk error during insert"):
        await sqlite_store.aappend_turn(
            session_id,
            "Simulated Fail",
            "Simulated Fail Answer",
            rewind_turn_index=1,
        )

    # After rollback, original turns (0, 1, 2) must still exist intact!
    turns_after = await sqlite_store.aget_turns(session_id)
    assert len(turns_after) == 3
    assert [t.turn_index for t in turns_after] == [0, 1, 2]
