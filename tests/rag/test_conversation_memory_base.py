"""Tests for domain DTOs, protocol seam, and configuration in the conversation memory subsystem."""

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.rag.memory.base import (
    ConversationMemoryAdapter,
    ConversationTurn,
    SessionInfo,
)
from app.rag.memory.noop import NoOpMemoryStore


def test_conversation_turn_creation_and_to_messages() -> None:
    """Validate ConversationTurn DTO validation, serialization, and LangChain message projection."""
    now = datetime.now(UTC)
    turn = ConversationTurn(
        session_id="session-123",
        turn_index=0,
        user_query="How do I reset my password?",
        assistant_answer="Go to the IT portal and click 'Reset Password'.",
        attachments=[],
        citations=[{"source": "it_guide.md", "snippet": "Password reset steps"}],
        source_used="vectorstore",
        trace_id="trace-abc",
        created_at=now,
    )

    assert turn.session_id == "session-123"
    assert turn.turn_index == 0
    assert turn.user_query == "How do I reset my password?"
    assert turn.assistant_answer == "Go to the IT portal and click 'Reset Password'."
    assert turn.attachments == []
    assert len(turn.citations) == 1
    assert turn.source_used == "vectorstore"
    assert turn.trace_id == "trace-abc"
    assert turn.created_at == now

    # Test to_messages projection without attachments
    messages = turn.to_messages()
    assert len(messages) == 2
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "How do I reset my password?"
    assert messages[0].additional_kwargs == {}
    assert isinstance(messages[1], AIMessage)
    assert messages[1].content == "Go to the IT portal and click 'Reset Password'."

    # Test to_messages projection with attachments
    attachments = [{"type": "image/png", "url": "https://example.com/screenshot.png"}]
    turn_with_att = ConversationTurn(
        session_id="session-123",
        turn_index=1,
        user_query="Here is the error screen",
        assistant_answer="I see the error code 403.",
        attachments=attachments,
    )
    messages_with_att = turn_with_att.to_messages()
    assert len(messages_with_att) == 2
    assert isinstance(messages_with_att[0], HumanMessage)
    assert messages_with_att[0].content == "Here is the error screen"
    assert messages_with_att[0].additional_kwargs == {"attachments": attachments}
    assert isinstance(messages_with_att[1], AIMessage)
    assert messages_with_att[1].content == "I see the error code 403."

    # Validate defaults
    turn_default = ConversationTurn(
        session_id="session-def",
        turn_index=2,
        user_query="Hello",
        assistant_answer="Hi",
    )
    assert turn_default.attachments == []
    assert turn_default.citations == []
    assert turn_default.source_used == "direct"
    assert turn_default.trace_id is None
    assert isinstance(turn_default.created_at, datetime)

    # Validate negative turn_index raises ValidationError
    with pytest.raises(ValidationError):
        ConversationTurn(
            session_id="session-def",
            turn_index=-1,
            user_query="Hello",
            assistant_answer="Hi",
        )


def test_session_info_validation() -> None:
    """Validate SessionInfo defaults and lineage attributes."""
    created = datetime.now(UTC)
    updated = datetime.now(UTC)

    # Test root session
    session = SessionInfo(
        session_id="session-root",
        title="Password Reset Query",
        created_at=created,
        updated_at=updated,
    )
    assert session.session_id == "session-root"
    assert session.title == "Password Reset Query"
    assert session.parent_session_id is None
    assert session.forked_from_turn is None
    assert session.created_at == created
    assert session.updated_at == updated
    assert session.turn_count == 0

    # Test branched/forked session
    forked_session = SessionInfo(
        session_id="session-child",
        title="Alternative Inquiry",
        parent_session_id="session-root",
        forked_from_turn=2,
        created_at=created,
        updated_at=updated,
        turn_count=3,
    )
    assert forked_session.parent_session_id == "session-root"
    assert forked_session.forked_from_turn == 2
    assert forked_session.turn_count == 3


def test_protocol_runtime_checkable() -> None:
    """Validate ConversationMemoryAdapter is runtime checkable and enforces deep seam methods."""

    class CompleteMemoryAdapter:
        async def aget_messages(self, session_id: str, limit: int | None = None, before_turn_index: int | None = None):
            return []

        async def aget_turns(self, session_id: str, limit: int | None = None, before_turn_index: int | None = None):
            return []

        async def asave_turn(self, turn: ConversationTurn) -> None:
            pass

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
            return ConversationTurn(
                session_id=session_id,
                turn_index=rewind_turn_index or 0,
                user_query=user_query,
                assistant_answer=assistant_answer,
            )

        async def aclear(self, session_id: str) -> None:
            pass

        async def atruncate_from(self, session_id: str, turn_index: int) -> None:
            pass

        async def afork_session(
            self, source_session_id: str, upto_turn_index: int, new_session_id: str | None = None
        ) -> str:
            return "forked-id"

        async def alist_sessions(self, limit: int = 50):
            return []

        async def aget_session(self, session_id: str):
            return None

    class IncompleteMemoryAdapter:
        async def aget_messages(self, session_id: str, limit: int | None = None):
            return []

    class MissingAppendTurnAdapter:
        async def aget_messages(self, session_id: str, limit: int | None = None, before_turn_index: int | None = None):
            return []

        async def aget_turns(self, session_id: str, limit: int | None = None, before_turn_index: int | None = None):
            return []

        async def asave_turn(self, turn: ConversationTurn) -> None:
            pass

        async def aclear(self, session_id: str) -> None:
            pass

        async def atruncate_from(self, session_id: str, turn_index: int) -> None:
            pass

        async def afork_session(
            self, source_session_id: str, upto_turn_index: int, new_session_id: str | None = None
        ) -> str:
            return "forked-id"

        async def alist_sessions(self, limit: int = 50):
            return []

        async def aget_session(self, session_id: str):
            return None

    dummy_complete = CompleteMemoryAdapter()
    assert isinstance(dummy_complete, ConversationMemoryAdapter)

    dummy_incomplete = IncompleteMemoryAdapter()
    assert not isinstance(dummy_incomplete, ConversationMemoryAdapter)

    dummy_missing_append = MissingAppendTurnAdapter()
    assert not isinstance(dummy_missing_append, ConversationMemoryAdapter)


@pytest.mark.asyncio
async def test_noop_memory_store_conformance_and_append_turn() -> None:
    """Validate NoOpMemoryStore protocol conformance, bounded message queries, and aappend_turn."""
    store = NoOpMemoryStore()
    assert isinstance(store, ConversationMemoryAdapter)

    # Validate aget_messages and aget_turns accept before_turn_index without error
    messages = await store.aget_messages("session-noop", limit=5, before_turn_index=2)
    assert messages == []

    turns = await store.aget_turns("session-noop", limit=5, before_turn_index=2)
    assert turns == []

    # Validate aappend_turn default turn_index = 0 and non-null ConversationTurn
    turn1 = await store.aappend_turn(
        session_id="session-noop",
        user_query="Hello",
        assistant_answer="World",
    )
    assert isinstance(turn1, ConversationTurn)
    assert turn1.session_id == "session-noop"
    assert turn1.turn_index == 0
    assert turn1.user_query == "Hello"
    assert turn1.assistant_answer == "World"
    assert turn1.attachments == []
    assert turn1.citations == []
    assert turn1.source_used == "direct"
    assert turn1.trace_id is None
    assert isinstance(turn1.created_at, datetime)

    # Validate aappend_turn with rewind_turn_index and rich metadata
    turn2 = await store.aappend_turn(
        session_id="session-noop",
        user_query="Rewound question",
        assistant_answer="Rewound answer",
        rewind_turn_index=3,
        attachments=[{"type": "doc", "name": "spec.pdf"}],
        citations=[{"source": "spec.pdf", "snippet": "requirements"}],
        source_used="vectorstore",
        trace_id="trace-xyz",
    )
    assert isinstance(turn2, ConversationTurn)
    assert turn2.session_id == "session-noop"
    assert turn2.turn_index == 3
    assert turn2.user_query == "Rewound question"
    assert turn2.assistant_answer == "Rewound answer"
    assert turn2.attachments == [{"type": "doc", "name": "spec.pdf"}]
    assert turn2.citations == [{"source": "spec.pdf", "snippet": "requirements"}]
    assert turn2.source_used == "vectorstore"
    assert turn2.trace_id == "trace-xyz"


def test_config_settings() -> None:
    """Validate conversation memory configuration settings and constraints."""
    base_kwargs: dict[str, Any] = {
        "llm_api_key": SecretStr("mock"),
        "embed_api_key": SecretStr("mock"),
        "jev_api_key": SecretStr("mock"),
        "tavily_api_key": SecretStr("mock"),
        "pinecone_api_key": SecretStr("mock"),
        "admin_api_key": SecretStr("mock"),
    }

    settings = Settings(**base_kwargs)

    # Defaults
    assert settings.conversation_memory_backend == "sqlite"
    assert settings.conversation_db_path == "data/conversations.db"
    assert settings.memory_max_turns == 10

    # Custom valid values
    custom_settings = Settings(
        **base_kwargs,
        conversation_memory_backend="in_memory",
        conversation_db_path="data/custom.db",
        memory_max_turns=20,
    )
    assert custom_settings.conversation_memory_backend == "in_memory"
    assert custom_settings.conversation_db_path == "data/custom.db"
    assert custom_settings.memory_max_turns == 20

    # Validation errors
    with pytest.raises(ValidationError):
        Settings(
            **base_kwargs,
            conversation_memory_backend="invalid_backend",  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError):
        Settings(
            **base_kwargs,
            memory_max_turns=0,  # ge=1
        )
