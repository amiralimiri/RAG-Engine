"""Unified evaluation engine implementation and test double."""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from collections.abc import Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.telemetry import get_langfuse_client
from app.rag.evaluation.base import EvaluationResult, Evaluator
from app.rag.evaluation.judge import JudgeModels, get_judge_models

logger = logging.getLogger(__name__)


class EvaluationEngine:
    """Deep module coordinating metric calculation, judge evaluation, and Langfuse scoring."""

    def __init__(
        self,
        settings: Settings | None = None,
        judge_provider: Callable[[], Any] | None = None,
        client: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.judge_provider = judge_provider or get_judge_models
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        return get_langfuse_client()

    def score_metric(
        self,
        trace_id: str,
        metric_name: str,
        score: float,
        comment: str | None = None,
    ) -> bool:
        """Submit a normalized score and optional comment to Langfuse."""
        try:
            client = self.client
            if not client:
                return False

            normalized_score = max(0.0, min(1.0, float(score)))

            if hasattr(client, "create_score") and callable(getattr(client, "create_score")):
                client.create_score(
                    trace_id=trace_id,
                    name=metric_name,
                    value=normalized_score,
                    comment=comment,
                )
                return True
            elif hasattr(client, "score") and callable(getattr(client, "score")):
                client.score(
                    trace_id=trace_id,
                    name=metric_name,
                    value=normalized_score,
                    comment=comment,
                )
                return True

            logger.warning("Langfuse client has no score or create_score method")
            return False
        except Exception as exc:
            logger.warning(
                "Failed to submit score '%s' to Langfuse for trace %s: %s",
                metric_name,
                trace_id,
                exc,
            )
            return False

    def _compute_metrics(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
    ) -> EvaluationResult:
        """Core measurement pipeline executed synchronously."""
        if not self.settings.langfuse_enabled:
            return EvaluationResult()

        if self.settings.eval_sample_rate <= 0.0:
            return EvaluationResult()

        if self.settings.eval_sample_rate < 1.0 and random.random() >= self.settings.eval_sample_rate:
            return EvaluationResult()

        if not question or not answer:
            return EvaluationResult()

        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase

        judge = self.judge_provider()
        judge_llm = judge.llm if hasattr(judge, "llm") else judge

        scores: dict[str, float] = {}
        details: dict[str, Any] = {}
        contexts = retrieved_contexts or []

        # 1. Faithfulness Metric (only if retrieved contexts exist and source is not direct)
        faith_val: float | None = None
        if "faithfulness" in self.settings.eval_metrics:
            if source_used != "direct" and contexts:
                try:
                    tc = LLMTestCase(input=question, actual_output=answer, retrieval_context=contexts)
                    fm = FaithfulnessMetric(threshold=0.5, model=judge_llm, async_mode=False)
                    try:
                        fm.measure(tc, _show_indicator=False)
                    except TypeError:
                        fm.measure(tc)
                    faith_val = max(0.0, min(1.0, float(fm.score)))
                    scores["faithfulness"] = faith_val
                    reason = getattr(fm, "reason", None) or "Evaluated via DeepEval LocalModel judge"
                    details["faithfulness_reason"] = reason

                    if trace_id:
                        self.score_metric(
                            trace_id=trace_id,
                            metric_name="faithfulness",
                            score=faith_val,
                            comment=reason,
                        )
                except Exception as exc:
                    logger.warning("Faithfulness calculation failed for trace %s: %s", trace_id, exc)

        # 2. Answer Relevancy Metric
        rel_val: float | None = None
        if "answer_relevancy" in self.settings.eval_metrics:
            try:
                tc = LLMTestCase(input=question, actual_output=answer)
                arm = AnswerRelevancyMetric(threshold=0.5, model=judge_llm, async_mode=False)
                try:
                    arm.measure(tc, _show_indicator=False)
                except TypeError:
                    arm.measure(tc)
                rel_val = max(0.0, min(1.0, float(arm.score)))
                scores["answer_relevancy"] = rel_val
                reason = getattr(arm, "reason", None) or "Evaluated via DeepEval LocalModel judge"
                details["answer_relevancy_reason"] = reason

                if trace_id:
                    self.score_metric(
                        trace_id=trace_id,
                        metric_name="answer_relevancy",
                        score=rel_val,
                        comment=reason,
                    )
            except Exception as exc:
                logger.warning("Answer relevancy calculation failed for trace %s: %s", trace_id, exc)

        return EvaluationResult(
            faithfulness=faith_val,
            answer_relevancy=rel_val,
            scores=scores,
            details=details,
        )

    def evaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
    ) -> EvaluationResult:
        """Synchronously execute metric calculation and score submission."""
        return self._compute_metrics(
            question=question,
            answer=answer,
            source_used=source_used,
            retrieved_contexts=retrieved_contexts,
            trace_id=trace_id,
        )

    async def aevaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
    ) -> EvaluationResult:
        """Asynchronously execute metric calculation using threadpool offloading."""
        return await run_in_threadpool(
            self._compute_metrics,
            question=question,
            answer=answer,
            source_used=source_used,
            retrieved_contexts=retrieved_contexts,
            trace_id=trace_id,
        )


class FakeEvaluator:
    """In-memory test double for Evaluator with configurable deterministic scores."""

    def __init__(
        self,
        faithfulness: float | None = 1.0,
        answer_relevancy: float | None = 1.0,
        custom_scores: dict[str, float] | None = None,
    ) -> None:
        self.faithfulness = faithfulness
        self.answer_relevancy = answer_relevancy
        self.custom_scores = custom_scores or {}
        self.evaluate_calls: list[dict[str, Any]] = []
        self.score_calls: list[dict[str, Any]] = []

    def evaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
    ) -> EvaluationResult:
        self.evaluate_calls.append({
            "question": question,
            "answer": answer,
            "source_used": source_used,
            "retrieved_contexts": retrieved_contexts,
            "trace_id": trace_id,
        })
        scores: dict[str, float] = dict(self.custom_scores)
        if self.faithfulness is not None:
            scores["faithfulness"] = self.faithfulness
        if self.answer_relevancy is not None:
            scores["answer_relevancy"] = self.answer_relevancy
        return EvaluationResult(
            faithfulness=self.faithfulness,
            answer_relevancy=self.answer_relevancy,
            scores=scores,
            details={"fake": True},
        )

    async def aevaluate(
        self,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
        trace_id: str | None = None,
    ) -> EvaluationResult:
        return self.evaluate(
            question=question,
            answer=answer,
            source_used=source_used,
            retrieved_contexts=retrieved_contexts,
            trace_id=trace_id,
        )

    def score_metric(
        self,
        trace_id: str,
        metric_name: str,
        score: float,
        comment: str | None = None,
    ) -> bool:
        self.score_calls.append({
            "trace_id": trace_id,
            "name": metric_name,
            "score": score,
            "comment": comment,
        })
        return True


_evaluator_instance: Evaluator | None = None
_evaluator_lock = threading.Lock()


def get_evaluator(
    settings: Settings | None = None,
    judge_provider: Callable[[], Any] | None = None,
    client: Any | None = None,
    evaluator: Evaluator | None = None,
) -> Evaluator:
    """Return the active Evaluator singleton with optional override injection."""
    global _evaluator_instance
    if evaluator is not None:
        _evaluator_instance = evaluator
        return _evaluator_instance

    if _evaluator_instance is None:
        with _evaluator_lock:
            if _evaluator_instance is None:
                _evaluator_instance = EvaluationEngine(
                    settings=settings,
                    judge_provider=judge_provider,
                    client=client,
                )
    return _evaluator_instance


def reset_evaluator() -> None:
    """Reset the global Evaluator singleton (for test isolation)."""
    global _evaluator_instance
    with _evaluator_lock:
        _evaluator_instance = None


__all__ = [
    "EvaluationEngine",
    "FakeEvaluator",
    "get_evaluator",
    "reset_evaluator",
]
