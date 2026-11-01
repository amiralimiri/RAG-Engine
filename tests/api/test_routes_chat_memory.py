"""Tests for conversational memory, session management, rewind, and fork endpoints in app/api/routes.py (Ticket 06)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import (
    SessionDetailResponse,
    SessionListResponse,
)
from app.main import app
from app.rag.memory.base import ConversationTurn
from app.rag.memory.in_memory import InMemoryConversationStore
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.runner import WorkflowRunner
from tests.conftest import MockWorkflowDefinition


@pytest.fixture
def memory_store(monkeypatch) -> InMemoryConversationStore:
    """Fixture providing an isolated in-memory conversation memory adapter."""
    store = InMemoryConversationStore()
    monkeypatch.setattr("app.rag.memory._memory_instance", store)
    runner = WorkflowRunner(registry=get_workflow_registry(), memory=store)
    monkeypatch.setattr("app.rag.workflows.runner._default_runner", runner)
    return store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestChatMemoryRoutes:
    """Validate conversation memory REST endpoints."""

    def test_get_sessions_empty(self, client: TestClient, memory_store: InMemoryConversationStore):
        """GET /api/chat/sessions returns 200 with an empty list when no sessions exist."""
        resp = client.get("/api/chat/sessions")
        assert resp.status_code == 200
        data = resp.json()
        validated = SessionListResponse(**data)
        assert validated.total == 0
        assert validated.sessions == []

    def test_get_session_not_found(self, client: TestClient, memory_store: InMemoryConversationStore):
        """GET /api/chat/{session_id} returns 404 when session does not exist."""
        resp = client.get("/api/chat/unknown-session-id")
        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"] == "Session not found"

    def test_save_turn_and_get_session_detail(
        self,
        client: TestClient,
        memory_store: InMemoryConversationStore,
        register_mock_workflow: Callable[..., MockWorkflowDefinition],
    ):
        """POST /api/chat persists turn, and GET /api/chat/{session_id} returns rich history with citations."""
        register_mock_workflow(
            "fix_single_jev",
            answer="VPN setup requires connecting to gateway vpn.internal.",
            citations=[{"title": "vpn_setup.pdf", "url": "http://docs/vpn", "type": "private_kb"}],
            source_used="private_kb",
        )

        chat_resp = client.post(
            "/api/chat",
            json={
                "question": "How do I configure the corporate VPN?",
                "session_id": "sess-vpn-01",
                "workflow": "fix_single_jev",
            },
        )
        assert chat_resp.status_code == 200
        chat_data = chat_resp.json()
        assert "VPN setup requires connecting" in chat_data["answer"]

        # Retrieve session detail
        get_resp = client.get("/api/chat/sess-vpn-01")
        assert get_resp.status_code == 200
        detail_data = get_resp.json()
        detail = SessionDetailResponse(**detail_data)

        assert detail.session_id == "sess-vpn-01"
        assert detail.turn_count == 1
        assert len(detail.turns) == 1

        turn = detail.turns[0]
        assert turn.turn_index == 0
        assert turn.user_query == "How do I configure the corporate VPN?"
        assert turn.assistant_answer == "VPN setup requires connecting to gateway vpn.internal."
        assert turn.source_used == "private_kb"
        assert len(turn.citations) == 1
        assert turn.citations[0]["title"] == "vpn_setup.pdf"

        # Also verify GET /api/chat/sessions includes this session
        list_resp = client.get("/api/chat/sessions")
        assert list_resp.status_code == 200
        list_data = SessionListResponse(**list_resp.json())
        assert list_data.total == 1
        assert list_data.sessions[0].session_id == "sess-vpn-01"
        assert list_data.sessions[0].turn_count == 1

    def test_delete_session(
        self,
        client: TestClient,
        memory_store: InMemoryConversationStore,
        register_mock_workflow: Callable[..., MockWorkflowDefinition],
    ):
        """DELETE /api/chat/{session_id} cascades deletion and subsequent GET returns 404."""
        register_mock_workflow("fix_single_jev", answer="Answer 1")

        client.post(
            "/api/chat",
            json={"question": "First question", "session_id": "sess-to-delete", "workflow": "fix_single_jev"},
        )

        # Confirm session exists
        check_resp = client.get("/api/chat/sess-to-delete")
        assert check_resp.status_code == 200

        # Delete session
        del_resp = client.delete("/api/chat/sess-to-delete")
        assert del_resp.status_code == 200
        assert del_resp.json() == {"status": "deleted", "session_id": "sess-to-delete"}

        # Verify subsequent GET returns 404
        post_del_resp = client.get("/api/chat/sess-to-delete")
        assert post_del_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rewind_session_endpoint(
        self,
        client: TestClient,
        memory_store: InMemoryConversationStore,
    ):
        """POST /api/chat/{session_id}/rewind truncates history from given turn index."""
        session_id = "sess-rewind-test"
        now = datetime.now(UTC)
        for i in range(3):
            await memory_store.asave_turn(
                ConversationTurn(
                    session_id=session_id,
                    turn_index=i,
                    user_query=f"Query {i}",
                    assistant_answer=f"Answer {i}",
                    created_at=now,
                )
            )

        detail_before = client.get(f"/api/chat/{session_id}").json()
        assert detail_before["turn_count"] == 3

        # Rewind from turn 1 (keeps turn 0, deletes 1 and 2)
        rewind_resp = client.post(
            f"/api/chat/{session_id}/rewind",
            json={"turn_index": 1},
        )
        assert rewind_resp.status_code == 200
        assert rewind_resp.json() == {
            "status": "rewound",
            "session_id": session_id,
            "turn_index": 1,
        }

        # Check remaining turns
        detail_after = client.get(f"/api/chat/{session_id}").json()
        assert detail_after["turn_count"] == 1
        assert len(detail_after["turns"]) == 1
        assert detail_after["turns"][0]["turn_index"] == 0

        # Rewind non-existent session returns 404
        not_found_resp = client.post(
            "/api/chat/non-existent-session/rewind",
            json={"turn_index": 0},
        )
        assert not_found_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_fork_session_endpoint(
        self,
        client: TestClient,
        memory_store: InMemoryConversationStore,
    ):
        """POST /api/chat/{session_id}/fork creates child session with lineage metadata and copied turns."""
        source_id = "sess-parent"
        now = datetime.now(UTC)
        for i in range(3):
            await memory_store.asave_turn(
                ConversationTurn(
                    session_id=source_id,
                    turn_index=i,
                    user_query=f"Parent query {i}",
                    assistant_answer=f"Parent answer {i}",
                    created_at=now,
                )
            )

        fork_resp = client.post(
            f"/api/chat/{source_id}/fork",
            json={"upto_turn_index": 1, "new_session_id": "sess-child-branch"},
        )
        assert fork_resp.status_code == 200
        child_data = fork_resp.json()
        child_detail = SessionDetailResponse(**child_data)

        assert child_detail.session_id == "sess-child-branch"
        assert child_detail.parent_session_id == source_id
        assert child_detail.forked_from_turn == 1
        assert child_detail.turn_count == 2
        assert len(child_detail.turns) == 2
        assert child_detail.turns[0].turn_index == 0
        assert child_detail.turns[1].turn_index == 1

        # Forking non-existent session returns 404
        nf_resp = client.post(
            "/api/chat/unknown-parent/fork",
            json={"upto_turn_index": 0},
        )
        assert nf_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_fork_turn_zero_endpoint(
        self,
        client: TestClient,
        memory_store: InMemoryConversationStore,
    ):
        """POST /api/chat/{session_id}/fork with upto_turn_index = -1 branches empty session tracking lineage."""
        source_id = "sess-root"
        now = datetime.now(UTC)
        await memory_store.asave_turn(
            ConversationTurn(
                session_id=source_id,
                turn_index=0,
                user_query="Root query 0",
                assistant_answer="Root answer 0",
                created_at=now,
            )
        )

        fork_resp = client.post(
            f"/api/chat/{source_id}/fork",
            json={"upto_turn_index": -1, "new_session_id": "sess-empty-branch"},
        )
        assert fork_resp.status_code == 200
        detail = SessionDetailResponse(**fork_resp.json())

        assert detail.session_id == "sess-empty-branch"
        assert detail.parent_session_id == source_id
        assert detail.forked_from_turn == -1
        assert detail.turn_count == 0
        assert detail.turns == []

    @pytest.mark.asyncio
    async def test_atomic_rewind_via_chat_post(
        self,
        client: TestClient,
        memory_store: InMemoryConversationStore,
        register_mock_workflow: Callable[..., MockWorkflowDefinition],
    ):
        """POST /api/chat with rewind_turn_index atomically truncates and saves replaced turn."""
        session_id = "sess-atomic-rewind"
        now = datetime.now(UTC)
        # Seed 2 turns
        for i in range(2):
            await memory_store.asave_turn(
                ConversationTurn(
                    session_id=session_id,
                    turn_index=i,
                    user_query=f"Initial query {i}",
                    assistant_answer=f"Initial answer {i}",
                    created_at=now,
                )
            )

        register_mock_workflow(
            "fix_single_jev",
            answer="Replaced answer for turn 1",
            source_used="direct",
        )

        # Execute edit-and-run rewind
        chat_resp = client.post(
            "/api/chat",
            json={
                "question": "Rewritten query for turn 1",
                "session_id": session_id,
                "workflow": "fix_single_jev",
                "rewind_turn_index": 1,
            },
        )
        assert chat_resp.status_code == 200

        # Verify session detail reflects replacement
        detail_resp = client.get(f"/api/chat/{session_id}")
        assert detail_resp.status_code == 200
        detail = SessionDetailResponse(**detail_resp.json())

        assert detail.turn_count == 2
        assert len(detail.turns) == 2
        assert detail.turns[0].user_query == "Initial query 0"
        assert detail.turns[1].turn_index == 1
        assert detail.turns[1].user_query == "Rewritten query for turn 1"
        assert detail.turns[1].assistant_answer == "Replaced answer for turn 1"
