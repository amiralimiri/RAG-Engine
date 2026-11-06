"""Tests for in-memory, no-op memory adapters and conversation memory factory."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.rag.memory import (
    ConversationMemoryAdapter,
    ConversationSession,
    ConversationTurn,
    InMemoryConversationStore,
    NoOpMemoryStore,
    SQLiteConversationStore,
    get_conversation_memory,
    reset_conversation_memory,
)
from app.rag.state import Citation


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

    # Forking returns an ephemeral ConversationSession without error
    fork_session_generated = await store.afork_session("session-noop", upto_turn_index=0)
    assert isinstance(fork_session_generated, ConversationSession)
    assert len(fork_session_generated.session_id) > 0
    assert fork_session_generated.turns == []
    assert fork_session_generated.turn_count == 0
    assert fork_session_generated.parent_session_id == "session-noop"
    assert fork_session_generated.forked_from_turn == 0

    fork_session_explicit = await store.afork_session(
        "session-noop", upto_turn_index=0, new_session_id="custom-child-id"
    )
    assert isinstance(fork_session_explicit, ConversationSession)
    assert fork_session_explicit.session_id == "custom-child-id"
    assert fork_session_explicit.turns == []
    assert fork_session_explicit.parent_session_id == "session-noop"
    assert fork_session_explicit.forked_from_turn == 0


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
        citations=[Citation(title="docker_guide.md")],
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
    forked_session = await store.afork_session("session-parent", upto_turn_index=1)
    assert isinstance(forked_session, ConversationSession)
    fork_id = forked_session.session_id
    assert fork_id != "session-parent"
    assert len(forked_session.turns) == 2
    assert [t.turn_index for t in forked_session.turns] == [0, 1]
    assert all(t.session_id == fork_id for t in forked_session.turns)
    assert forked_session.parent_session_id == "session-parent"
    assert forked_session.forked_from_turn == 1
    assert forked_session.turn_count == 2
    assert "(Fork)" in forked_session.title or "Parent query 0" in forked_session.title

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
    assert isinstance(child_session, ConversationSession)
    assert child_session.parent_session_id == "session-parent"
    assert child_session.forked_from_turn == 1
    assert child_session.turn_count == 2
    assert "(Fork)" in child_session.title or "Parent query 0" in child_session.title
    assert len(child_session.turns) == 2


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

    forked_session = await store.afork_session(
        "session-source",
        upto_turn_index=-1,
        new_session_id="session-zero-child",
    )
    assert isinstance(forked_session, ConversationSession)
    assert forked_session.session_id == "session-zero-child"
    assert forked_session.turns == []
    assert forked_session.parent_session_id == "session-source"
    assert forked_session.forked_from_turn == -1
    assert forked_session.turn_count == 0

    # Child has 0 turns
    child_turns = await store.aget_turns("session-zero-child")
    assert child_turns == []

    # Lineage is preserved
    child_session = await store.aget_session("session-zero-child")
    assert child_session is not None
    assert isinstance(child_session, ConversationSession)
    assert child_session.session_id == "session-zero-child"
    assert child_session.parent_session_id == "session-source"
    assert child_session.forked_from_turn == -1
    assert child_session.turn_count == 0
    assert child_session.turns == []


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


@pytest.mark.asyncio
async def test_in_memory_append_turn_monotonic_indices() -> None:
    """Validate that sequential aappend_turn calls assign monotonic turn indices 0, 1, 2 and update session."""
    store = InMemoryConversationStore()
    session_id = "session-append-seq"

    turn_0 = await store.aappend_turn(
        session_id=session_id,
        user_query="First question",
        assistant_answer="First answer",
    )
    assert turn_0.turn_index == 0
    assert turn_0.session_id == session_id
    assert turn_0.user_query == "First question"
    assert turn_0.assistant_answer == "First answer"

    turn_1 = await store.aappend_turn(
        session_id=session_id,
        user_query="Second question",
        assistant_answer="Second answer",
    )
    assert turn_1.turn_index == 1

    turn_2 = await store.aappend_turn(
        session_id=session_id,
        user_query="Third question",
        assistant_answer="Third answer",
    )
    assert turn_2.turn_index == 2

    session = await store.aget_session(session_id)
    assert session is not None
    assert session.turn_count == 3
    assert session.title == "First question"

    turns = await store.aget_turns(session_id)
    assert len(turns) == 3
    assert [t.turn_index for t in turns] == [0, 1, 2]


@pytest.mark.asyncio
async def test_in_memory_append_turn_rewind_truncation() -> None:
    """Validate that aappend_turn with rewind_turn_index deletes turns >= k and persists replacement turn at k."""
    store = InMemoryConversationStore()
    session_id = "session-append-rewind"

    # Append 3 turns: 0, 1, 2
    for idx in range(3):
        await store.aappend_turn(
            session_id=session_id,
            user_query=f"Original Q{idx}",
            assistant_answer=f"Original A{idx}",
        )

    # Rewind at turn 1 with new question/answer
    rewound_turn = await store.aappend_turn(
        session_id=session_id,
        user_query="Rewound Q1",
        assistant_answer="Rewound A1",
        rewind_turn_index=1,
    )

    assert rewound_turn.turn_index == 1
    assert rewound_turn.user_query == "Rewound Q1"
    assert rewound_turn.assistant_answer == "Rewound A1"

    session = await store.aget_session(session_id)
    assert session is not None
    assert session.turn_count == 2

    turns = await store.aget_turns(session_id)
    assert len(turns) == 2
    assert [t.turn_index for t in turns] == [0, 1]
    assert turns[0].user_query == "Original Q0"
    assert turns[1].user_query == "Rewound Q1"

    # Now append again without rewind; should get index 2
    next_turn = await store.aappend_turn(
        session_id=session_id,
        user_query="New Q2",
        assistant_answer="New A2",
    )
    assert next_turn.turn_index == 2
    res_session = await store.aget_session(session_id)
    assert res_session is not None
    assert res_session.turn_count == 3


@pytest.mark.asyncio
async def test_in_memory_bounded_retrieval_before_turn_index() -> None:
    """Validate that aget_turns and aget_messages filter by before_turn_index and apply limit to the tail."""
    store = InMemoryConversationStore()
    session_id = "session-bounded-before"

    for idx in range(5):
        await store.aappend_turn(
            session_id=session_id,
            user_query=f"Query {idx}",
            assistant_answer=f"Answer {idx}",
        )

    # All turns: [0, 1, 2, 3, 4]
    # Filter before_turn_index=3 => turns [0, 1, 2]
    turns_before_3 = await store.aget_turns(session_id, before_turn_index=3)
    assert len(turns_before_3) == 3
    assert [t.turn_index for t in turns_before_3] == [0, 1, 2]

    # Filter before_turn_index=2 and limit=1 => turn [1]
    turns_bounded = await store.aget_turns(session_id, limit=1, before_turn_index=2)
    assert len(turns_bounded) == 1
    assert turns_bounded[0].turn_index == 1
    assert turns_bounded[0].user_query == "Query 1"

    # Filter messages before_turn_index=2 and limit=1 => 2 messages corresponding to turn 1
    messages_bounded = await store.aget_messages(session_id, limit=1, before_turn_index=2)
    assert len(messages_bounded) == 2
    assert messages_bounded[0].content == "Query 1"
    assert messages_bounded[1].content == "Answer 1"

    # Filter before_turn_index=0 => empty list
    turns_before_0 = await store.aget_turns(session_id, before_turn_index=0)
    assert turns_before_0 == []
    messages_before_0 = await store.aget_messages(session_id, before_turn_index=0)
    assert messages_before_0 == []


@pytest.mark.asyncio
async def test_in_memory_append_turn_preserves_attachments_citations_and_metadata() -> None:
    """Validate that multi-turn append with attachments, citations, source, and trace_id preserves all attributes."""
    store = InMemoryConversationStore()
    session_id = "session-rich-append"

    attachments = [{"name": "spec.pdf", "type": "application/pdf"}]
    citations = [Citation(title="kb/doc1.md", url="", type="general")]

    turn = await store.aappend_turn(
        session_id=session_id,
        user_query="Explain the spec",
        assistant_answer="According to spec.pdf...",
        attachments=attachments,
        citations=citations,
        source_used="vectorstore",
        trace_id="trace-12345",
    )

    assert turn.turn_index == 0
    assert turn.attachments == attachments
    assert turn.citations == [Citation(title="kb/doc1.md", url="", type="general")]
    assert turn.citations[0].title == "kb/doc1.md"
    assert turn.citations[0].url == ""
    assert turn.citations[0].type == "general"
    assert turn.source_used == "vectorstore"
    assert turn.trace_id == "trace-12345"

    # Verify retrieval preserves all attributes
    retrieved_turns = await store.aget_turns(session_id)
    assert len(retrieved_turns) == 1
    stored = retrieved_turns[0]
    assert stored.attachments == attachments
    assert stored.citations == [Citation(title="kb/doc1.md", url="", type="general")]
    assert stored.citations[0].title == "kb/doc1.md"
    assert stored.citations[0].url == ""
    assert stored.citations[0].type == "general"
    assert stored.source_used == "vectorstore"
    assert stored.trace_id == "trace-12345"

    # Verify deep copy isolation: mutating returned turn does not affect store
    turn.attachments.append({"name": "mutated.txt"})
    fresh_turns = await store.aget_turns(session_id)
    assert len(fresh_turns[0].attachments) == 1


@pytest.mark.asyncio
async def test_in_memory_aget_session_full_aggregate_and_limit_windowing() -> None:
    """Validate that aget_session returns a complete ConversationSession with chronological turns and limit windowing."""
    store = InMemoryConversationStore()
    session_id = "session-aggregate-test"

    # Non-existent session returns None
    assert await store.aget_session("missing-session") is None
    assert await store.aget_session("missing-session", limit=5) is None

    # Seed 5 turns: 0, 1, 2, 3, 4
    for idx in range(5):
        await store.aappend_turn(
            session_id=session_id,
            user_query=f"Question {idx}",
            assistant_answer=f"Answer {idx}",
        )

    # 1. Full aggregate without limit
    full_session = await store.aget_session(session_id)
    assert full_session is not None
    assert isinstance(full_session, ConversationSession)
    assert full_session.session_id == session_id
    assert full_session.turn_count == 5
    assert len(full_session.turns) == 5
    assert [t.turn_index for t in full_session.turns] == [0, 1, 2, 3, 4]

    # 2. Limit windowing: limit=2 returns latest 2 turns ordered ascending
    limited_session = await store.aget_session(session_id, limit=2)
    assert limited_session is not None
    assert isinstance(limited_session, ConversationSession)
    assert limited_session.session_id == session_id
    assert limited_session.turn_count == 5  # Total session turn count preserved
    assert len(limited_session.turns) == 2
    assert [t.turn_index for t in limited_session.turns] == [3, 4]
    assert limited_session.turns[0].user_query == "Question 3"
    assert limited_session.turns[1].user_query == "Question 4"

    # 3. Limit=0 returns empty turns list
    zero_limited = await store.aget_session(session_id, limit=0)
    assert zero_limited is not None
    assert zero_limited.turns == []
    assert zero_limited.turn_count == 5

    # 4. Limit exceeding turn count returns all turns
    large_limited = await store.aget_session(session_id, limit=10)
    assert large_limited is not None
    assert len(large_limited.turns) == 5
    assert [t.turn_index for t in large_limited.turns] == [0, 1, 2, 3, 4]

    # 5. Deep copy isolation: mutating turns on returned aggregate does not mutate store
    full_session.turns[0].user_query = "Mutated Question"
    re_fetched = await store.aget_session(session_id)
    assert re_fetched is not None
    assert re_fetched.turns[0].user_query == "Question 0"


@pytest.mark.asyncio
async def test_in_memory_afork_session_aggregate_and_invariants() -> None:
    """Validate afork_session preconditions, KeyError, ValueError, and aggregate return."""
    store = InMemoryConversationStore()
    source_id = "session-parent-invariants"

    # Invariant: source session not found raises KeyError
    with pytest.raises(KeyError, match=f"Session not found: {source_id}"):
        await store.afork_session(source_id, upto_turn_index=0)

    # Invariant: upto_turn_index < -1 raises ValueError
    with pytest.raises(ValueError, match=r"Invalid upto_turn_index: -2\. Must be >= -1\."):
        await store.afork_session(source_id, upto_turn_index=-2)

    # Seed parent session with 4 turns
    for idx in range(4):
        await store.aappend_turn(
            session_id=source_id,
            user_query=f"Parent Q{idx}",
            assistant_answer=f"Parent A{idx}",
        )

    # Invariant: even with existing parent, upto_turn_index < -1 raises ValueError
    with pytest.raises(ValueError, match=r"Invalid upto_turn_index: -5\. Must be >= -1\."):
        await store.afork_session(source_id, upto_turn_index=-5)

    # Valid fork at turn 2 (turns 0, 1, 2)
    custom_child_id = "custom-fork-aggregate"
    forked = await store.afork_session(source_id, upto_turn_index=2, new_session_id=custom_child_id)
    assert isinstance(forked, ConversationSession)
    assert forked.session_id == custom_child_id
    assert forked.parent_session_id == source_id
    assert forked.forked_from_turn == 2
    assert forked.turn_count == 3
    assert len(forked.turns) == 3
    assert [t.turn_index for t in forked.turns] == [0, 1, 2]
    assert all(t.session_id == custom_child_id for t in forked.turns)
    assert forked.turns[2].user_query == "Parent Q2"
    assert "(Fork)" in forked.title

    # Mutating returned forked turns does not affect stored child session
    forked.turns[0].user_query = "Corrupted Q0"
    stored_child = await store.aget_session(custom_child_id)
    assert stored_child is not None
    assert stored_child.turns[0].user_query == "Parent Q0"


@pytest.mark.asyncio
async def test_noop_memory_store_aget_session_and_afork_session() -> None:
    """Validate that NoOpMemoryStore aget_session returns None and afork_session returns ephemeral ConversationSession."""
    store = NoOpMemoryStore()

    # aget_session returns None regardless of session_id or limit
    assert await store.aget_session("any-session") is None
    assert await store.aget_session("any-session", limit=10) is None

    # afork_session returns ephemeral ConversationSession with empty turns
    ephemeral_generated = await store.afork_session("source-session", upto_turn_index=3)
    assert isinstance(ephemeral_generated, ConversationSession)
    assert len(ephemeral_generated.session_id) > 0
    assert ephemeral_generated.title == "Ephemeral Fork"
    assert ephemeral_generated.parent_session_id == "source-session"
    assert ephemeral_generated.forked_from_turn == 3
    assert ephemeral_generated.turn_count == 0
    assert ephemeral_generated.turns == []

    # afork_session with explicit child id
    ephemeral_explicit = await store.afork_session("source-session", upto_turn_index=-1, new_session_id="child-123")
    assert isinstance(ephemeral_explicit, ConversationSession)
    assert ephemeral_explicit.session_id == "child-123"
    assert ephemeral_explicit.parent_session_id == "source-session"
    assert ephemeral_explicit.forked_from_turn == -1
    assert ephemeral_explicit.turn_count == 0
    assert ephemeral_explicit.turns == []


@pytest.mark.asyncio
async def test_in_memory_citations_typed_across_append_rewind_fork() -> None:
    """Validate that in-memory append, rewind (atruncate_from), and afork_session preserve typed Citation instances."""
    store = InMemoryConversationStore()
    session_id = "session-in-memory-citations"
    c0 = Citation(title="Doc 0", url="https://example.com/0", type="web")
    c1 = Citation(title="Doc 1", url="https://example.com/1", type="kb")
    c2 = Citation(title="Doc 2", url="https://example.com/2", type="kb")

    # 1. Append turns
    t0 = await store.aappend_turn(
        session_id=session_id,
        user_query="Q0",
        assistant_answer="A0",
        citations=[c0],
    )
    assert len(t0.citations) == 1
    assert isinstance(t0.citations[0], Citation)
    assert t0.citations[0].title == "Doc 0"
    assert t0.citations[0].url == "https://example.com/0"
    assert t0.citations[0].type == "web"
    assert t0.citations[0] == c0

    t1 = await store.aappend_turn(
        session_id=session_id,
        user_query="Q1",
        assistant_answer="A1",
        citations=[c1],
    )
    assert t1.citations[0] == c1

    t2 = await store.aappend_turn(
        session_id=session_id,
        user_query="Q2",
        assistant_answer="A2",
        citations=[c2],
    )
    assert t2.citations[0] == c2

    # 2. Fork session up to turn 1
    forked = await store.afork_session(session_id, upto_turn_index=1)
    assert len(forked.turns) == 2
    for turn, expected_citation in zip(forked.turns, [c0, c1]):
        assert len(turn.citations) == 1
        assert isinstance(turn.citations[0], Citation)
        assert turn.citations[0].title == expected_citation.title
        assert turn.citations[0].url == expected_citation.url
        assert turn.citations[0].type == expected_citation.type
        assert turn.citations[0] == expected_citation

    # Verify retrieved fork turns from store
    forked_turns = await store.aget_turns(forked.session_id)
    assert len(forked_turns) == 2
    assert forked_turns[0].citations[0] == c0
    assert forked_turns[1].citations[0] == c1

    # 3. Rewind (truncate from turn 2)
    await store.atruncate_from(session_id, turn_index=2)
    surviving_turns = await store.aget_turns(session_id)
    assert len(surviving_turns) == 2
    assert surviving_turns[0].citations[0] == c0
    assert surviving_turns[1].citations[0] == c1
    assert isinstance(surviving_turns[0].citations[0], Citation)
    assert isinstance(surviving_turns[1].citations[0], Citation)
