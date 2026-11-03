"""Unit tests for ExecutionLifecycleListener protocol and concrete adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.evaluation import EvaluationResult, Evaluator
from app.rag.memory import ConversationMemoryAdapter, InMemoryConversationStore
from app.rag.workflows.base import WorkflowInput, WorkflowOutput
from app.rag.workflows.listeners import (
    BaseExecutionLifecycleListener,
    ExecutionLifecycleListener,
    MemoryPersistenceListener,
    OnlineEvaluationListener,
)


class TestExecutionLifecycleListenerProtocol:
    """Tests verifying the ExecutionLifecycleListener protocol contract and base no-op behavior."""

    def test_protocol_runtime_checkable(self) -> None:
        """Verify ExecutionLifecycleListener can be checked at runtime via isinstance."""
        base_listener = BaseExecutionLifecycleListener()
        assert isinstance(base_listener, ExecutionLifecycleListener)

    def test_custom_listener_satisfies_protocol(self) -> None:
        """Verify custom class defining protocol hooks satisfies ExecutionLifecycleListener."""

        class CustomListener:
            async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
                pass

            async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
                pass

            async def on_error(self, request: WorkflowInput, error: Exception) -> None:
                pass

            async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
                pass

        assert isinstance(CustomListener(), ExecutionLifecycleListener)

    def test_incomplete_class_does_not_satisfy_protocol(self) -> None:
        """Verify class missing required lifecycle hooks fails isinstance check."""

        class IncompleteListener:
            async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
                pass

        assert not isinstance(IncompleteListener(), ExecutionLifecycleListener)

    @pytest.mark.asyncio
    async def test_base_listener_methods_are_noop(self) -> None:
        """Verify BaseExecutionLifecycleListener methods execute cleanly as no-ops."""
        listener = BaseExecutionLifecycleListener()
        request = WorkflowInput(question="What is RAG?")
        output = WorkflowOutput(workflow="test_wf", answer="RAG is Retrieval-Augmented Generation.")

        # None of the hooks should raise
        await listener.on_completed(request, output)
        await listener.on_background_task(request, output)
        await listener.on_error(request, RuntimeError("Something went wrong"))
        await listener.on_cancelled(request, output)
        await listener.on_cancelled(request, None)


class TestMemoryPersistenceListener:
    """Tests for MemoryPersistenceListener turn persistence, cancellation, and validation."""

    def test_protocol_conformance(self) -> None:
        """Verify MemoryPersistenceListener satisfies ExecutionLifecycleListener protocol."""
        store = InMemoryConversationStore()
        listener = MemoryPersistenceListener(memory=store)
        assert isinstance(listener, ExecutionLifecycleListener)
        assert isinstance(listener, BaseExecutionLifecycleListener)

    @pytest.mark.asyncio
    async def test_on_completed_persists_turn_when_session_and_answer_present(self) -> None:
        """Verify atomic turn persistence via aappend_turn when session_id and answer are present."""
        store = InMemoryConversationStore()
        listener = MemoryPersistenceListener(memory=store)

        session_id = "test-session-completed"
        request = WorkflowInput(
            question="What is LangGraph?",
            session_id=session_id,
            trace_id="req-trace-1",
            rewind_turn_index=None,
            attachments=[{"name": "doc.pdf", "type": "pdf"}],
        )
        output = WorkflowOutput(
            workflow="react_agent",
            answer="LangGraph is an agentic framework.",
            source_used="private_kb",
            citations=[{"id": "doc_1", "text": "evidence"}],
            trace_id="out-trace-1",
        )

        await listener.on_completed(request, output)

        turns = await store.aget_turns(session_id)
        assert len(turns) == 1
        turn = turns[0]
        assert turn.session_id == session_id
        assert turn.turn_index == 0
        assert turn.user_query == "What is LangGraph?"
        assert turn.assistant_answer == "LangGraph is an agentic framework."
        assert turn.source_used == "private_kb"
        assert turn.citations == [{"id": "doc_1", "text": "evidence"}]
        assert turn.attachments == [{"name": "doc.pdf", "type": "pdf"}]
        assert turn.trace_id == "out-trace-1"

    @pytest.mark.asyncio
    async def test_on_completed_with_rewind_turn_index(self) -> None:
        """Verify turn persistence respects rewind_turn_index on turn replacement."""
        store = InMemoryConversationStore()
        listener = MemoryPersistenceListener(memory=store)
        session_id = "test-session-rewind"

        # Pre-populate two turns (0 and 1)
        await store.aappend_turn(session_id=session_id, user_query="Q0", assistant_answer="A0")
        await store.aappend_turn(session_id=session_id, user_query="Q1", assistant_answer="A1")

        turns_before = await store.aget_turns(session_id)
        assert len(turns_before) == 2

        # Rewind to turn 1 and execute replacement
        request = WorkflowInput(
            question="Replacement Q1",
            session_id=session_id,
            rewind_turn_index=1,
        )
        output = WorkflowOutput(
            workflow="fix_single_llm",
            answer="Replacement A1",
        )

        await listener.on_completed(request, output)

        turns_after = await store.aget_turns(session_id)
        assert len(turns_after) == 2
        assert turns_after[0].user_query == "Q0"
        assert turns_after[1].user_query == "Replacement Q1"
        assert turns_after[1].turn_index == 1

    @pytest.mark.asyncio
    async def test_on_completed_aborts_without_session_id(self) -> None:
        """Verify turn persistence does not trigger when session_id is None or missing."""
        mock_memory = AsyncMock(spec=ConversationMemoryAdapter)
        listener = MemoryPersistenceListener(memory=mock_memory)

        request = WorkflowInput(question="Standalone query without session", session_id=None)
        output = WorkflowOutput(workflow="fix_single_jev", answer="Answer text")

        await listener.on_completed(request, output)
        mock_memory.aappend_turn.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sentinel", ["", "   ", "undefined", "null", "NULL", "Undefined"])
    async def test_on_completed_aborts_on_sentinel_session_id(self, sentinel: str) -> None:
        """Verify turn persistence sanitizes session_id and aborts on blank/sentinel strings."""
        mock_memory = AsyncMock(spec=ConversationMemoryAdapter)
        listener = MemoryPersistenceListener(memory=mock_memory)

        request = WorkflowInput(question="Sentinel session query", session_id=sentinel)
        output = WorkflowOutput(workflow="fix_single_jev", answer="Answer text")

        await listener.on_completed(request, output)
        mock_memory.aappend_turn.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("blank_answer", ["", "   "])
    async def test_on_completed_aborts_when_answer_is_blank(self, blank_answer: str) -> None:
        """Verify turn persistence does not persist empty or whitespace-only answers."""
        mock_memory = AsyncMock(spec=ConversationMemoryAdapter)
        listener = MemoryPersistenceListener(memory=mock_memory)

        request = WorkflowInput(question="Question", session_id="session-123")
        output = WorkflowOutput(workflow="fix_single_jev", answer=blank_answer)

        await listener.on_completed(request, output)
        mock_memory.aappend_turn.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_completed_noops_when_memory_is_none(self) -> None:
        """Verify listener safely no-ops without exception when memory is not configured."""
        listener = MemoryPersistenceListener(memory=None)
        request = WorkflowInput(question="Question", session_id="session-123")
        output = WorkflowOutput(workflow="fix_single_jev", answer="Answer")

        await listener.on_completed(request, output)

    @pytest.mark.asyncio
    async def test_on_completed_catches_persistence_errors(self) -> None:
        """Verify exceptions in memory store are safely caught and logged without propagating."""
        mock_memory = AsyncMock(spec=ConversationMemoryAdapter)
        mock_memory.aappend_turn.side_effect = RuntimeError("Disk I/O failure")
        listener = MemoryPersistenceListener(memory=mock_memory)

        request = WorkflowInput(question="Question", session_id="session-123")
        output = WorkflowOutput(workflow="fix_single_jev", answer="Answer")

        # Must not raise RuntimeError
        await listener.on_completed(request, output)
        mock_memory.aappend_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_cancelled_discards_partial_turns(self) -> None:
        """Verify cancellation hook discards partial turns and does NOT call aappend_turn."""
        mock_memory = AsyncMock(spec=ConversationMemoryAdapter)
        listener = MemoryPersistenceListener(memory=mock_memory)

        request = WorkflowInput(question="Cancelled question", session_id="session-123")
        partial_output = WorkflowOutput(workflow="fix_single_jev", answer="Partial stream chunk")

        await listener.on_cancelled(request, partial_output)
        await listener.on_cancelled(request, None)
        mock_memory.aappend_turn.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_error_does_not_persist(self) -> None:
        """Verify error hook does NOT persist turns to memory."""
        mock_memory = AsyncMock(spec=ConversationMemoryAdapter)
        listener = MemoryPersistenceListener(memory=mock_memory)

        request = WorkflowInput(question="Failing question", session_id="session-123")
        await listener.on_error(request, RuntimeError("Graph node failed"))
        mock_memory.aappend_turn.assert_not_called()


class TestOnlineEvaluationListener:
    """Tests for OnlineEvaluationListener background execution, error shielding, and formatting."""

    def test_protocol_conformance(self) -> None:
        """Verify OnlineEvaluationListener satisfies ExecutionLifecycleListener protocol."""
        listener = OnlineEvaluationListener()
        assert isinstance(listener, ExecutionLifecycleListener)
        assert isinstance(listener, BaseExecutionLifecycleListener)

    @pytest.mark.asyncio
    async def test_on_background_task_dispatches_eval_task_fn_async(self) -> None:
        """Verify async eval_task_fn is invoked with trace_id, question, answer, source, and contexts."""
        mock_eval_task = AsyncMock()
        listener = OnlineEvaluationListener(eval_task_fn=mock_eval_task)

        request = WorkflowInput(question="Explain transformers", trace_id="req-trace-123")
        output = WorkflowOutput(
            workflow="fix_single_llm",
            answer="Transformers use self-attention mechanisms.",
            source_used="private_kb",
            citations=[{"content": "Doc 1 text"}, {"text": "Doc 2 text"}],
            trace_id="out-trace-456",
        )

        await listener.on_background_task(request, output)

        mock_eval_task.assert_awaited_once_with(
            trace_id="out-trace-456",
            question="Explain transformers",
            answer="Transformers use self-attention mechanisms.",
            source_used="private_kb",
            retrieved_contexts=["Doc 1 text", "Doc 2 text"],
        )

    @pytest.mark.asyncio
    async def test_on_background_task_dispatches_eval_task_fn_sync(self) -> None:
        """Verify synchronous eval_task_fn is safely invoked and executed."""
        mock_eval_task = MagicMock(return_value=None)
        listener = OnlineEvaluationListener(eval_task_fn=mock_eval_task)

        request = WorkflowInput(question="Explain transformers", trace_id="trace-sync")
        output = WorkflowOutput(
            workflow="react_agent",
            answer="Synchronous answer",
            source_used="web",
            citations=[],
        )

        await listener.on_background_task(request, output)
        mock_eval_task.assert_called_once_with(
            trace_id="trace-sync",
            question="Explain transformers",
            answer="Synchronous answer",
            source_used="web",
            retrieved_contexts=[],
        )

    @pytest.mark.asyncio
    async def test_on_background_task_dispatches_evaluator_aevaluate(self) -> None:
        """Verify evaluator.aevaluate is invoked when eval_task_fn is omitted."""
        mock_evaluator = AsyncMock(spec=Evaluator)
        mock_evaluator.aevaluate.return_value = EvaluationResult(faithfulness=1.0)
        listener = OnlineEvaluationListener(evaluator=mock_evaluator)

        request = WorkflowInput(question="What is RAG?", trace_id="trace-eval")
        output = WorkflowOutput(
            workflow="fix_single_jev",
            answer="RAG combines retrieval with LLMs.",
            source_used="private_kb",
            citations=[{"snippet": "Snippet text"}],
        )

        await listener.on_background_task(request, output)

        mock_evaluator.aevaluate.assert_awaited_once_with(
            question="What is RAG?",
            answer="RAG combines retrieval with LLMs.",
            source_used="private_kb",
            retrieved_contexts=["Snippet text"],
            trace_id="trace-eval",
        )

    @pytest.mark.asyncio
    async def test_on_background_task_with_custom_context_extractor(self) -> None:
        """Verify custom context extractor hook takes precedence over citations."""
        mock_eval_task = AsyncMock()
        custom_extractor = MagicMock(return_value=["Custom Context 1", "Custom Context 2"])
        listener = OnlineEvaluationListener(
            eval_task_fn=mock_eval_task,
            context_extractor=custom_extractor,
        )

        request = WorkflowInput(question="Query")
        output = WorkflowOutput(
            workflow="fix_single_llm",
            answer="Answer",
            citations=[{"content": "Ignored Citation"}],
            trace_id="trace-custom",
        )

        await listener.on_background_task(request, output)

        custom_extractor.assert_called_once_with(output)
        mock_eval_task.assert_awaited_once_with(
            trace_id="trace-custom",
            question="Query",
            answer="Answer",
            source_used="direct",
            retrieved_contexts=["Custom Context 1", "Custom Context 2"],
        )

    @pytest.mark.asyncio
    async def test_on_background_task_skips_dict_citations_without_text(self) -> None:
        """Verify dict citations without standard content keys are safely ignored."""
        mock_eval_task = AsyncMock()
        listener = OnlineEvaluationListener(eval_task_fn=mock_eval_task)

        request = WorkflowInput(question="Query")
        output = WorkflowOutput(
            workflow="fix_single_llm",
            answer="Answer",
            citations=[{"text": "Direct text context 1"}, {"title": "Doc without text"}],
            trace_id="trace-str",
        )

        await listener.on_background_task(request, output)

        call_kwargs = mock_eval_task.call_args.kwargs
        assert call_kwargs["retrieved_contexts"] == ["Direct text context 1"]

    @pytest.mark.asyncio
    async def test_on_background_task_noops_when_no_evaluator_or_fn(self) -> None:
        """Verify listener safely no-ops without error when neither evaluator nor eval_task_fn configured."""
        listener = OnlineEvaluationListener(evaluator=None, eval_task_fn=None)

        request = WorkflowInput(question="Query")
        output = WorkflowOutput(workflow="fix_single_llm", answer="Answer")

        await listener.on_background_task(request, output)

    @pytest.mark.asyncio
    async def test_on_background_task_shields_exceptions(self) -> None:
        """Verify exceptions thrown by eval_task_fn or evaluator are shielded and logged without raising."""
        failing_eval_task = AsyncMock(side_effect=RuntimeError("Judge API 500 error"))
        listener = OnlineEvaluationListener(eval_task_fn=failing_eval_task)

        request = WorkflowInput(question="Query", trace_id="trace-failing")
        output = WorkflowOutput(workflow="fix_single_llm", answer="Answer")

        # Must not raise RuntimeError
        await listener.on_background_task(request, output)
        failing_eval_task.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_other_lifecycle_hooks_are_noop(self) -> None:
        """Verify on_completed, on_error, and on_cancelled do not invoke eval_task_fn."""
        mock_eval_task = AsyncMock()
        listener = OnlineEvaluationListener(eval_task_fn=mock_eval_task)

        request = WorkflowInput(question="Query")
        output = WorkflowOutput(workflow="fix_single_llm", answer="Answer")

        await listener.on_completed(request, output)
        await listener.on_error(request, RuntimeError("Graph failed"))
        await listener.on_cancelled(request, output)
        await listener.on_cancelled(request, None)

        mock_eval_task.assert_not_called()


class TestWorkflowPackageExports:
    """Tests verifying public exports from app.rag.workflows package root."""

    def test_listeners_exported_from_package(self) -> None:
        """Verify listeners are exposed at app.rag.workflows level and declared in __all__."""
        import app.rag.workflows as wf_pkg

        exported_symbols = [
            "ExecutionLifecycleListener",
            "BaseExecutionLifecycleListener",
            "MemoryPersistenceListener",
            "OnlineEvaluationListener",
        ]

        for symbol in exported_symbols:
            assert hasattr(wf_pkg, symbol), f"{symbol} should be exported by app.rag.workflows"
            assert symbol in wf_pkg.__all__, f"{symbol} should be declared in app.rag.workflows.__all__"
            # Verify the exported object is the exact same object from listeners.py
            from app.rag.workflows import listeners

            assert getattr(wf_pkg, symbol) is getattr(listeners, symbol)



