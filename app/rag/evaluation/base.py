"""Base protocols and data models for the RAG evaluation subsystem."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    """Normalized results returned from evaluation runs."""

    faithfulness: float | None = None
    answer_relevancy: float | None = None
    source_accuracy: float | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Evaluator(Protocol):
    """Protocol seam defining metric evaluation and score submission."""

    def evaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
        *,
        expected_output: str | None = None,
        expected_source: str | None = None,
    ) -> EvaluationResult:
        """Synchronously measure metrics and optionally push scores to Langfuse."""
        ...

    async def aevaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
        *,
        expected_output: str | None = None,
        expected_source: str | None = None,
    ) -> EvaluationResult:
        """Asynchronously measure metrics with threadpool offloading and push scores."""
        ...

    def score_metric(
        self,
        trace_id: str,
        metric_name: str,
        score: float,
        comment: str | None = None,
    ) -> bool:
        """Submit a normalized score (0.0 to 1.0) and optional comment for a trace."""
        ...


__all__ = [
    "EvaluationResult",
    "Evaluator",
]
