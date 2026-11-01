"""Tests for in-memory, no-op memory adapters and conversation memory factory."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.rag.memory import (
    ConversationMemoryAdapter,
    ConversationTurn,
    InMemoryConversationStore,
    NoOpMemoryStore,
    SQLiteConversationStore,
    get_conversation_memory,
    reset_conversation_memory,
)


@pytest.mark.asyncio
async def test_noop_memory_store_protocol_compliance_and_behavior() -> None:
    """Validate that NoOpMemoryStore conforms to ConversationMemoryAdapter and acts as a safe no-op."""
    store = NoOpMemoryStore()
    assert isinstance(store, ConversationMemoryAdapter)

    # Empty retrievals
    messages = await store.aget_messages("session-noop", limit=10)
    assert messages == []

    turns = await store.aget_turns("session-noop", limit=5)
    assert turns == []

    session = await store.aget_session("session-noop")
    assert session is None

    sessions = await store.alist_sessions(limit=20)
    assert sessions == []

    # Safe write operations
    turn = ConversationTurn(
        session_id="session-noop",
        turn_index=0,
        user_query="Hello",
        assistant_answer="World",
    )
    await store.asave_turn(turn)
    await store.atruncate_from("session-noop", turn_index=0)
    await store.aclear("session-noop")

    # Forking returns an ID without error
    fork_id_generated = await store.afork_session("session-noop", upto_turn_index=0)
    assert isinstance(fork_id_generated, str)
    assert len(fork_id_generated) > 0

    fork_id_explicit = await store.afork_session("session-noop", upto_turn_index=0, new_session_id="custom-child-id")
    assert fork_id_explicit == "custom-child-id"


@pytest.mark.asyncio
async def test_in_memory_save_and_get_turns_and_messages() -> None:
    """Validate turn persistence, auto-session creation, and message projection."""
    store = InMemoryConversationStore()
    assert isinstance(store, ConversationMemoryAdapter)

    turn_0 = ConversationTurn(
        session_id="session-1",
        turn_index=0,
        user_query="How do I setup Docker?",
        assistant_answer="Install Docker Desktop and start the daemon.",
        citations=[{"source": "docker_guide.md"}],
        source_used="vectorstore",
    )
    turn_1 = ConversationTurn(
        session_id="session-1",
        turn_index=1,
        user_query="Can I use Docker Compose?",
        assistant_answer="Yes, Docker Compose is included by default.",
    )

    # Persist in reverse order to check sorting invariant
    await store.asave_turn(turn_1)
    await store.asave_turn(turn_0)

    turns = await store.aget_turns("session-1")
    assert len(turns) == 2
    assert turns[0].turn_index == 0
    assert turns[0].user_query == "How do I setup Docker?"
    assert turns[1].turn_index == 1
    assert turns[1].user_query == "Can I use Docker Compose?"

    # Validate message projection
    messages = await store.aget_messages("session-1")
    assert len(messages) == 4
    assert messages[0].content == "How do I setup Docker?"
    assert messages[1].content == "Install Docker Desktop and start the daemon."
    assert messages[2].content == "Can I use Docker Compose?"
    assert messages[3].content == "Yes, Docker Compose is included by default."

    # Validate auto-created session info
    session = await store.aget_session("session-1")
    assert session is not None
    assert session.session_id == "session-1"
    assert session.turn_count == 2
    assert "Docker" in session.title


@pytest.mark.asyncio
async def test_in_memory_bounded_retrieval() -> None:
    """Validate that limit returns the latest limit turns/messages."""
    store = InMemoryConversationStore()

    for idx in range(5):
        await store.asave_turn(
            ConversationTurn(
                session_id="session-bounded",
                turn_index=idx,
                user_query=f"Question {idx}",
                assistant_answer=f"Answer {idx}",
            )
        )

    # All turns
    all_turns = await store.aget_turns("session-bounded")
    assert len(all_turns) == 5

    # Bounded turns (limit=2 => turns 3 and 4)
    bounded_turns = await store.aget_turns("session-bounded", limit=2)
    assert len(bounded_turns) == 2
    assert bounded_turns[0].turn_index == 3
    assert bounded_turns[1].turn_index == 4

    # Bounded messages (limit=2 => 4 messages from turns 3 and 4)
    bounded_messages = await store.aget_messages("session-bounded", limit=2)
    assert len(bounded_messages) == 4
    assert bounded_messages[0].content == "Question 3"
    assert bounded_messages[1].content == "Answer 3"
    assert bounded_messages[2].content == "Question 4"
    assert bounded_messages[3].content == "Answer 4"

    # Non-existent session returns empty lists
    assert await store.aget_turns("non-existent") == []
    assert await store.aget_messages("non-existent") == []

    # limit=0 returns empty list
    assert await store.aget_turns("session-bounded", limit=0) == []
    assert await store.aget_messages("session-bounded", limit=0) == []


@pytest.mark.asyncio
async def test_in_memory_upsert_turn() -> None:
    """Validate that saving a turn with existing turn_index replaces it."""
    store = InMemoryConversationStore()

    turn_initial = ConversationTurn(
        session_id="session-upsert",
        turn_index=0,
        user_query="Original question",
        assistant_answer="Original answer",
    )
    await store.asave_turn(turn_initial)

    turn_updated = ConversationTurn(
        session_id="session-upsert",
        turn_index=0,
        user_query="Original question",
        assistant_answer="Updated replacement answer",
    )
    await store.asave_turn(turn_updated)

    turns = await store.aget_turns("session-upsert")
    assert len(turns) == 1
    assert turns[0].assistant_answer == "Updated replacement answer"


@pytest.mark.asyncio
async def test_in_memory_truncate_from() -> None:
    """Validate that atruncate_from deletes turns where turn_index >= k and updates turn_count."""
    store = InMemoryConversationStore()

    for idx in range(4):
        await store.asave_turn(
            ConversationTurn(
                session_id="session-trunc",
                turn_index=idx,
                user_query=f"Query {idx}",
                assistant_answer=f"Answer {idx}",
            )
        )

    # Initial state
    initial_turns = await store.aget_turns("session-trunc")
    assert len(initial_turns) == 4
    session_before = await store.aget_session("session-trunc")
    assert session_before is not None
    assert session_before.turn_count == 4

    # Truncate from turn_index=2
    await store.atruncate_from("session-trunc", turn_index=2)

    remaining_turns = await store.aget_turns("session-trunc")
    assert len(remaining_turns) == 2
    assert [t.turn_index for t in remaining_turns] == [0, 1]

    session_after = await store.aget_session("session-trunc")
    assert session_after is not None
    assert session_after.turn_count == 2

    # Truncate non-existent session safely
    await store.atruncate_from("non-existent", turn_index=0)


@pytest.mark.asyncio
async def test_in_memory_clear_session() -> None:
    """Validate that aclear purges all turns and session metadata."""
    store = InMemoryConversationStore()

    await store.asave_turn(
        ConversationTurn(
            session_id="session-clear",
            turn_index=0,
            user_query="To be deleted",
            assistant_answer="Deleting...",
        )
    )

    assert await store.aget_session("session-clear") is not None
    assert len(await store.aget_turns("session-clear")) == 1

    await store.aclear("session-clear")

    assert await store.aget_session("session-clear") is None
    assert await store.aget_turns("session-clear") == []
    assert await store.aget_messages("session-clear") == []

    # Safe to call on non-existent session
    await store.aclear("session-clear")


@pytest.mark.asyncio
async def test_in_memory_list_sessions_recency_and_limit() -> None:
    """Validate that alist_sessions returns sessions ordered by updated_at descending with limit."""
    store = InMemoryConversationStore()

    base_time = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    # Create 3 sessions with distinct updated_at times
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

    await store.asave_turn(turn_old)
    await store.asave_turn(turn_mid)
    await store.asave_turn(turn_new)

    # List all
    sessions = await store.alist_sessions(limit=10)
    assert len(sessions) == 3
    assert [s.session_id for s in sessions] == ["session-new", "session-mid", "session-old"]

    # List with limit=2
    bounded_sessions = await store.alist_sessions(limit=2)
    assert len(bounded_sessions) == 2
    assert [s.session_id for s in bounded_sessions] == ["session-new", "session-mid"]


@pytest.mark.asyncio
async def test_in_memory_fork_session() -> None:
    """Validate conversation branching with turn cloning and lineage tracking."""
    store = InMemoryConversationStore()

    for idx in range(4):
        await store.asave_turn(
            ConversationTurn(
                session_id="session-parent",
                turn_index=idx,
                user_query=f"Parent query {idx}",
                assistant_answer=f"Parent answer {idx}",
            )
        )

    # Fork at turn 1 (copying turns 0 and 1)
    fork_id = await store.afork_session("session-parent", upto_turn_index=1)
    assert fork_id != "session-parent"

    # Parent remains unaffected
    parent_turns = await store.aget_turns("session-parent")
    assert len(parent_turns) == 4

    # Child has turns 0 and 1 re-bound to fork_id
    child_turns = await store.aget_turns(fork_id)
    assert len(child_turns) == 2
    assert [t.turn_index for t in child_turns] == [0, 1]
    assert all(t.session_id == fork_id for t in child_turns)
    assert child_turns[0].user_query == "Parent query 0"
    assert child_turns[1].user_query == "Parent query 1"

    # Verify session lineage
    child_session = await store.aget_session(fork_id)
    assert child_session is not None
    assert child_session.parent_session_id == "session-parent"
    assert child_session.forked_from_turn == 1
    assert child_session.turn_count == 2
    assert "(Fork)" in child_session.title or "Parent query 0" in child_session.title


@pytest.mark.asyncio
async def test_in_memory_fork_session_turn_zero() -> None:
    """Validate turn 0 branching (upto_turn_index = -1) creating empty child with lineage."""
    store = InMemoryConversationStore()

    await store.asave_turn(
        ConversationTurn(
            session_id="session-source",
            turn_index=0,
            user_query="Initial query",
            assistant_answer="Initial answer",
        )
    )

    fork_id = await store.afork_session(
        "session-source",
        upto_turn_index=-1,
        new_session_id="session-zero-child",
    )
    assert fork_id == "session-zero-child"

    # Child has 0 turns
    child_turns = await store.aget_turns(fork_id)
    assert child_turns == []

    # Lineage is preserved
    child_session = await store.aget_session(fork_id)
    assert child_session is not None
    assert child_session.session_id == "session-zero-child"
    assert child_session.parent_session_id == "session-source"
    assert child_session.forked_from_turn == -1
    assert child_session.turn_count == 0


@pytest.mark.asyncio
async def test_in_memory_concurrent_access() -> None:
    """Validate thread-safe concurrent saves and retrievals across multiple sessions."""
    import asyncio

    store = InMemoryConversationStore()

    async def worker(worker_id: int) -> None:
        session_id = f"session-worker-{worker_id}"
        for turn_idx in range(10):
            turn = ConversationTurn(
                session_id=session_id,
                turn_index=turn_idx,
                user_query=f"Worker {worker_id} query {turn_idx}",
                assistant_answer=f"Worker {worker_id} answer {turn_idx}",
            )
            await store.asave_turn(turn)
            # Interleave retrieval
            messages = await store.aget_messages(session_id, limit=3)
            assert len(messages) <= 6

    await asyncio.gather(*(worker(i) for i in range(10)))

    # Verify all 10 sessions exist with 10 turns each
    sessions = await store.alist_sessions(limit=50)
    assert len(sessions) == 10
    for session in sessions:
        assert session.turn_count == 10
        turns = await store.aget_turns(session.session_id)
        assert len(turns) == 10


def test_factory_resolution_and_singleton(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Validate factory adapter resolution, backend settings, and singleton reset."""
    base_kwargs: dict[str, Any] = {
        "llm_api_key": SecretStr("mock"),
        "embed_api_key": SecretStr("mock"),
        "jev_api_key": SecretStr("mock"),
        "tavily_api_key": SecretStr("mock"),
        "pinecone_api_key": SecretStr("mock"),
        "admin_api_key": SecretStr("mock"),
    }

    # Backend resolution from Settings: in_memory
    settings_in_mem = Settings(**base_kwargs, conversation_memory_backend="in_memory")
    mem_in_memory = get_conversation_memory(settings=settings_in_mem)
    assert isinstance(mem_in_memory, InMemoryConversationStore)

    # Backend resolution from Settings: noop
    settings_noop = Settings(**base_kwargs, conversation_memory_backend="noop")
    mem_noop = get_conversation_memory(settings=settings_noop)
    assert isinstance(mem_noop, NoOpMemoryStore)

    # Backend resolution from Settings: sqlite
    settings_sqlite = Settings(
        **base_kwargs,
        conversation_memory_backend="sqlite",
        conversation_db_path=str(tmp_path / "factory.db"),
    )
    mem_sqlite = get_conversation_memory(settings=settings_sqlite)
    assert isinstance(mem_sqlite, SQLiteConversationStore)

    # Singleton caching when called without settings
    monkeypatch.setenv("CONVERSATION_MEMORY_BACKEND", "noop")
    from app.core.config import get_settings

    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    reset_conversation_memory()
    singleton_1 = get_conversation_memory()
    singleton_2 = get_conversation_memory()
    assert singleton_1 is singleton_2
    assert isinstance(singleton_1, NoOpMemoryStore)

    # reset_conversation_memory resets singleton
    reset_conversation_memory()
    monkeypatch.setenv("CONVERSATION_MEMORY_BACKEND", "in_memory")
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    singleton_3 = get_conversation_memory()
    assert isinstance(singleton_3, InMemoryConversationStore)
    assert singleton_3 is not singleton_1





