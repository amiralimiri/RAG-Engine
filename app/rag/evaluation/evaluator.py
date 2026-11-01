"""Unified evaluation engine implementation and test double."""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from starlette.concurrency import run_in_threadpool

import app.core.telemetry as telemetry
from app.core.config import Settings, get_settings
from app.rag.evaluation.base import EvaluationResult, Evaluator
from app.rag.evaluation.judge import get_judge_models

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
        if not self.settings.langfuse_enabled:
            return None
        try:
            return telemetry.get_langfuse_client(self.settings)
        except TypeError:
            return telemetry.get_langfuse_client()

    def score_metric(
        self,
        trace_id: str,
        metric_name: str,
        score: float,
        comment: str | None = None,
    ) -> bool:
        """Submit a normalized score and optional comment to Langfuse."""
        if not self.settings.langfuse_enabled:
            return False
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
        expected_output: str | None = None,
        expected_source: str | None = None,
    ) -> EvaluationResult:
        """Core measurement pipeline executed synchronously."""
        if not question or not answer:
            return EvaluationResult()

        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase

        judge = self.judge_provider()
        judge_llm = judge.llm if hasattr(judge, "llm") else judge

        scores: dict[str, float] = {}
        details: dict[str, Any] = {}
        contexts = retrieved_contexts or []

        source_acc_val: float | None = None
        if expected_source is not None:
            details["expected_source"] = expected_source
            is_match = (
                (source_used.strip().lower() == expected_source.strip().lower())
                if (source_used and expected_source)
                else (source_used == expected_source)
            )
            source_acc_val = 1.0 if is_match else 0.0
            scores["source_accuracy"] = source_acc_val
            details["source_accuracy"] = source_acc_val

        if expected_output is not None:
            details["expected_output"] = expected_output

        should_run_faith = "faithfulness" in self.settings.eval_metrics and source_used != "direct" and bool(contexts)
        should_run_rel = "answer_relevancy" in self.settings.eval_metrics

        should_dispatch_scores = bool(
            trace_id
            and self.settings.langfuse_enabled
            and self.settings.eval_sample_rate > 0.0
            and (self.settings.eval_sample_rate >= 1.0 or random.random() < self.settings.eval_sample_rate)
        )

        if expected_source is not None and source_acc_val is not None and should_dispatch_scores and trace_id:
            self.score_metric(
                trace_id=trace_id,
                metric_name="source_accuracy",
                score=source_acc_val,
                comment=f"Expected: {expected_source}, Actual: {source_used}",
            )

        def _measure_metric(
            metric_class: type[Any],
            metric_name: str,
            test_case: Any,
        ) -> tuple[float | None, str | None]:
            try:
                metric = metric_class(threshold=0.5, model=judge_llm, async_mode=False)
                try:
                    metric.measure(test_case, _show_indicator=False)
                except TypeError:
                    metric.measure(test_case)
                val = max(0.0, min(1.0, float(metric.score)))
                reason = getattr(metric, "reason", None) or "Evaluated via DeepEval LocalModel judge"
                if should_dispatch_scores and trace_id:
                    self.score_metric(
                        trace_id=trace_id,
                        metric_name=metric_name,
                        score=val,
                        comment=reason,
                    )
                return val, reason
            except Exception as exc:
                logger.warning("%s calculation failed for trace %s: %s", metric_name, trace_id, exc)
                return None, None

        def _calc_faith() -> tuple[float | None, str | None]:
            tc = LLMTestCase(input=question, actual_output=answer, retrieval_context=cast(Any, contexts))
            return _measure_metric(FaithfulnessMetric, "faithfulness", tc)

        def _calc_rel() -> tuple[float | None, str | None]:
            tc = LLMTestCase(input=question, actual_output=answer)
            return _measure_metric(AnswerRelevancyMetric, "answer_relevancy", tc)

        faith_val: float | None = None
        rel_val: float | None = None

        if should_run_faith and should_run_rel:
            with ThreadPoolExecutor(max_workers=2) as executor:
                f_future = executor.submit(_calc_faith)
                r_future = executor.submit(_calc_rel)
                faith_val, faith_reason = f_future.result()
                rel_val, rel_reason = r_future.result()
        elif should_run_faith:
            faith_val, faith_reason = _calc_faith()
            rel_reason = None
        elif should_run_rel:
            rel_val, rel_reason = _calc_rel()
            faith_reason = None
        else:
            faith_reason = None
            rel_reason = None

        if faith_val is not None:
            scores["faithfulness"] = faith_val
        if faith_reason:
            details["faithfulness_reason"] = faith_reason

        if rel_val is not None:
            scores["answer_relevancy"] = rel_val
        if rel_reason:
            details["answer_relevancy_reason"] = rel_reason

        return EvaluationResult(
            faithfulness=faith_val,
            answer_relevancy=rel_val,
            source_accuracy=source_acc_val,
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
        *,
        expected_output: str | None = None,
        expected_source: str | None = None,
    ) -> EvaluationResult:
        """Synchronously execute metric calculation and score submission."""
        return self._compute_metrics(
            question=question,
            answer=answer,
            source_used=source_used,
            retrieved_contexts=retrieved_contexts,
            trace_id=trace_id,
            expected_output=expected_output,
            expected_source=expected_source,
        )

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
        """Asynchronously execute metric calculation using threadpool offloading and timeout shielding."""
        timeout = getattr(self.settings, "eval_timeout_seconds", 0.0)
        timeout_val = float(timeout) if timeout and float(timeout) > 0 else None
        coro = run_in_threadpool(
            self._compute_metrics,
            question=question,
            answer=answer,
            source_used=source_used,
            retrieved_contexts=retrieved_contexts,
            trace_id=trace_id,
            expected_output=expected_output,
            expected_source=expected_source,
        )
        try:
            if timeout_val:
                return await asyncio.wait_for(coro, timeout=timeout_val)
            return await coro
        except TimeoutError:
            logger.warning(
                "Online evaluation timed out after %.1fs for trace %s",
                timeout_val,
                trace_id,
            )
            return EvaluationResult()
        except Exception as exc:
            logger.warning(
                "Unexpected error during online evaluation for trace %s: %s",
                trace_id,
                exc,
            )
            return EvaluationResult()


class FakeEvaluator:
    """In-memory test double for Evaluator with configurable deterministic scores."""

    def __init__(
        self,
        faithfulness: float | None = 1.0,
        answer_relevancy: float | None = 1.0,
        source_accuracy: float | None = None,
        custom_scores: dict[str, float] | None = None,
    ) -> None:
        self.faithfulness = faithfulness
        self.answer_relevancy = answer_relevancy
        self.source_accuracy = source_accuracy
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
        *,
        expected_output: str | None = None,
        expected_source: str | None = None,
    ) -> EvaluationResult:
        self.evaluate_calls.append(
            {
                "question": question,
                "answer": answer,
                "source_used": source_used,
                "retrieved_contexts": retrieved_contexts,
                "trace_id": trace_id,
                "expected_output": expected_output,
                "expected_source": expected_source,
            }
        )
        scores: dict[str, float] = dict(self.custom_scores)
        if self.faithfulness is not None:
            scores["faithfulness"] = self.faithfulness
        if self.answer_relevancy is not None:
            scores["answer_relevancy"] = self.answer_relevancy

        source_acc_val = self.source_accuracy
        if source_acc_val is None and expected_source is not None:
            is_match = (
                (source_used.strip().lower() == expected_source.strip().lower())
                if (source_used and expected_source)
                else (source_used == expected_source)
            )
            source_acc_val = 1.0 if is_match else 0.0

        if source_acc_val is not None:
            scores["source_accuracy"] = source_acc_val

        details: dict[str, Any] = {"fake": True}
        if expected_output is not None:
            details["expected_output"] = expected_output
        if expected_source is not None:
            details["expected_source"] = expected_source
        if source_acc_val is not None:
            details["source_accuracy"] = source_acc_val

        if expected_source is not None and trace_id and source_acc_val is not None:
            self.score_metric(
                trace_id=trace_id,
                metric_name="source_accuracy",
                score=source_acc_val,
                comment=f"Expected: {expected_source}, Actual: {source_used}",
            )

        return EvaluationResult(
            faithfulness=self.faithfulness,
            answer_relevancy=self.answer_relevancy,
            source_accuracy=source_acc_val,
            scores=scores,
            details=details,
        )

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
        return self.evaluate(
            question=question,
            answer=answer,
            source_used=source_used,
            retrieved_contexts=retrieved_contexts,
            trace_id=trace_id,
            expected_output=expected_output,
            expected_source=expected_source,
        )

    def score_metric(
        self,
        trace_id: str,
        metric_name: str,
        score: float,
        comment: str | None = None,
    ) -> bool:
        self.score_calls.append(
            {
                "trace_id": trace_id,
                "name": metric_name,
                "score": score,
                "comment": comment,
            }
        )
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
