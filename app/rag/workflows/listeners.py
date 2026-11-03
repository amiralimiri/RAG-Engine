"""Pluggable execution lifecycle listener protocol and standard adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

import structlog

from app.rag.evaluation import Evaluator
from app.rag.memory.base import ConversationMemoryAdapter
from app.rag.workflows.base import WorkflowInput, WorkflowOutput

logger = structlog.get_logger(__name__)


@runtime_checkable
class ExecutionLifecycleListener(Protocol):
    """Protocol for receiving execution lifecycle notifications and executing side-effects."""

    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Invoked when execution succeeds. Awaited before terminal event emission."""
        ...

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Invoked concurrently in a detached background task without blocking response."""
        ...

    async def on_error(self, request: WorkflowInput, error: Exception) -> None:
        """Invoked when execution fails before terminal event emission."""
        ...

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        """Invoked when execution is cancelled by client disconnect."""
        ...


class BaseExecutionLifecycleListener:
    """Default no-op implementation of ExecutionLifecycleListener."""

    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """No-op on completion."""

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """No-op in background."""

    async def on_error(self, request: WorkflowInput, error: Exception) -> None:
        """No-op on error."""

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        """No-op on cancellation."""


class MemoryPersistenceListener(BaseExecutionLifecycleListener):
    """Lifecycle listener handling atomic conversation turn persistence and rewind."""

    def __init__(self, memory: ConversationMemoryAdapter | None = None) -> None:
        self._memory = memory

    @property
    def memory(self) -> ConversationMemoryAdapter | None:
        """The underlying conversation memory adapter."""
        return self._memory

    async def on_completed(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Awaits atomic turn persistence when session_id and answer are present."""
        if self._memory is None:
            return

        raw_session_id = request.session_id or output.session_id
        if not raw_session_id:
            return

        session_id = str(raw_session_id).strip()
        if not session_id or session_id.lower() in ("undefined", "null"):
            return

        if not output.answer or not output.answer.strip():
            return

        trace_id = output.trace_id or request.trace_id
        source_used = output.source_used or "direct"

        try:
            turn = await self._memory.aappend_turn(
                session_id=session_id,
                user_query=request.question,
                assistant_answer=output.answer,
                rewind_turn_index=request.rewind_turn_index,
                attachments=request.attachments or [],
                citations=output.citations,
                source_used=source_used,
                trace_id=trace_id,
            )
            logger.info(
                "conversation_turn_persisted",
                session_id=session_id,
                turn_index=turn.turn_index,
                source_used=source_used,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.warning(
                "turn_persistence_failed",
                session_id=session_id,
                error=str(exc),
            )

    async def on_cancelled(self, request: WorkflowInput, partial_output: WorkflowOutput | None) -> None:
        """Discards partial turns on cancellation without saving to memory store."""
        session_id = request.session_id or (partial_output.session_id if partial_output else None)
        logger.info(
            "execution_cancelled_turn_discarded",
            session_id=session_id,
        )


class OnlineEvaluationListener(BaseExecutionLifecycleListener):
    """Lifecycle listener safely dispatching online judge evaluation in the background."""

    def __init__(
        self,
        *,
        evaluator: Evaluator | None = None,
        eval_task_fn: Callable[..., Any] | None = None,
        context_extractor: Callable[[WorkflowOutput], list[str]] | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._eval_task_fn = eval_task_fn
        self._context_extractor = context_extractor

    @property
    def evaluator(self) -> Evaluator | None:
        """The underlying evaluator protocol instance if explicitly configured."""
        return self._evaluator

    @property
    def eval_task_fn(self) -> Callable[..., Any] | None:
        """The custom evaluation task callable if provided."""
        return self._eval_task_fn

    def _extract_contexts(self, output: WorkflowOutput) -> list[str]:
        """Extract retrieved context strings from citations or custom extractor."""
        if self._context_extractor is not None:
            return self._context_extractor(output)

        if getattr(output, "retrieved_contexts", None):
            return list(output.retrieved_contexts)

        contexts: list[str] = []
        for c in output.citations:
            if isinstance(c, dict):
                text = c.get("content") or c.get("text") or c.get("snippet")
                if text:
                    contexts.append(str(text))
            elif isinstance(c, str) and c.strip():
                contexts.append(c)
        return contexts

    async def on_background_task(self, request: WorkflowInput, output: WorkflowOutput) -> None:
        """Dispatches judge model evaluation safely without throwing exceptions."""
        if self._eval_task_fn is None and self._evaluator is None:
            return

        if not output.answer or not output.answer.strip():
            return

        trace_id = output.trace_id or getattr(request, "trace_id", None)
        if not trace_id:
            return
        question = request.question
        answer = output.answer
        source_used = output.source_used or "direct"
        retrieved_contexts = self._extract_contexts(output)

        try:
            if self._eval_task_fn is not None:
                res = self._eval_task_fn(
                    trace_id=trace_id,
                    question=question,
                    answer=answer,
                    source_used=source_used,
                    retrieved_contexts=retrieved_contexts,
                )
            elif self._evaluator is not None:
                res = self._evaluator.aevaluate(
                    question=question,
                    answer=answer,
                    source_used=source_used,
                    retrieved_contexts=retrieved_contexts,
                    trace_id=trace_id,
                )
            else:
                return

            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                await res
        except Exception as exc:
            logger.warning(
                "online_evaluation_failed_shielded",
                trace_id=trace_id,
                error=str(exc),
            )


__all__ = [
    "BaseExecutionLifecycleListener",
    "ExecutionLifecycleListener",
    "MemoryPersistenceListener",
    "OnlineEvaluationListener",
]
