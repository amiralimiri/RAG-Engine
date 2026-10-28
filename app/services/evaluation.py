import asyncio
import logging
import random
from typing import Optional

from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase
from starlette.concurrency import run_in_threadpool

from app.core import telemetry
from app.core.config import get_settings
from evaluation.judge import judge

logger = logging.getLogger(__name__)


def get_langfuse_client():
    return telemetry.get_langfuse_client()


def score_trace_metric(
    trace_id: str,
    metric_name: str,
    score: float,
    comment: Optional[str] = None,
) -> bool:
    """Submit a normalized score and optional comment for a given trace to Langfuse."""
    try:
        client = get_langfuse_client()
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


def score_trace_faithfulness(
    trace_id: str,
    question: str,
    answer: str,
    retrieved_contexts: list[str],
) -> Optional[float]:
    """Runs faithfulness evaluation via judge and scores the Langfuse trace."""
    client = get_langfuse_client()
    if not client or not question or not answer or not retrieved_contexts:
        return None

    try:
        test_case = LLMTestCase(
            input=question,
            actual_output=answer,
            retrieval_context=retrieved_contexts,
        )
        metric = FaithfulnessMetric(
            threshold=0.5,
            model=judge.llm,
            async_mode=False,
        )
        try:
            metric.measure(test_case, _show_indicator=False)
        except TypeError:
            metric.measure(test_case)
        score_val = max(0.0, min(1.0, float(metric.score)))
        comment = getattr(metric, "reason", None) or "Evaluated via DeepEval LocalModel judge"

        submitted = score_trace_metric(
            trace_id=trace_id,
            metric_name="faithfulness",
            score=score_val,
            comment=comment,
        )
        if not submitted:
            return None
        return score_val
    except Exception as exc:
        logger.warning(
            "Failed to evaluate and score faithfulness for trace %s: %s",
            trace_id,
            exc,
        )
        return None


def score_trace_answer_relevancy(
    trace_id: str,
    question: str,
    answer: str,
) -> Optional[float]:
    """Runs answer relevancy evaluation via judge and scores the Langfuse trace."""
    client = get_langfuse_client()
    if not client or not question or not answer:
        return None

    try:
        test_case = LLMTestCase(
            input=question,
            actual_output=answer,
        )
        metric = AnswerRelevancyMetric(
            threshold=0.5,
            model=judge.llm,
            async_mode=False,
        )
        try:
            metric.measure(test_case, _show_indicator=False)
        except TypeError:
            metric.measure(test_case)
        score_val = max(0.0, min(1.0, float(metric.score)))
        comment = getattr(metric, "reason", None) or "Evaluated via DeepEval LocalModel judge"

        submitted = score_trace_metric(
            trace_id=trace_id,
            metric_name="answer_relevancy",
            score=score_val,
            comment=comment,
        )
        if not submitted:
            return None
        return score_val
    except Exception as exc:
        logger.warning(
            "Failed to evaluate and score answer relevancy for trace %s: %s",
            trace_id,
            exc,
        )
        return None


async def run_online_evaluation_task(
    trace_id: Optional[str],
    question: str,
    answer: str,
    source_used: str,
    retrieved_contexts: Optional[list[str]] = None,
) -> None:
    """Asynchronously evaluate live chat completions and push scores to Langfuse."""
    if not trace_id:
        return

    settings = get_settings()
    if not settings.langfuse_enabled:
        return

    # 1. Deterministic Sampling Gate
    if settings.eval_sample_rate <= 0.0:
        return
    if settings.eval_sample_rate < 1.0 and random.random() >= settings.eval_sample_rate:
        return

    logger.info("Triggering online DeepEval evaluation for trace %s", trace_id)

    contexts = retrieved_contexts or []

    async def _eval_faithfulness() -> None:
        try:
            await run_in_threadpool(
                score_trace_faithfulness,
                trace_id=trace_id,
                question=question,
                answer=answer,
                retrieved_contexts=contexts,
            )
        except Exception as exc:
            logger.warning(
                "Faithfulness evaluation failed for trace %s: %s",
                trace_id,
                exc,
            )

    async def _eval_answer_relevancy() -> None:
        try:
            await run_in_threadpool(
                score_trace_answer_relevancy,
                trace_id=trace_id,
                question=question,
                answer=answer,
            )
        except Exception as exc:
            logger.warning(
                "Answer relevancy evaluation failed for trace %s: %s",
                trace_id,
                exc,
            )

    async def _evaluate() -> None:
        tasks = []
        # Evaluate Faithfulness only when retrieval took place and context is present
        if "faithfulness" in settings.eval_metrics:
            if source_used != "direct" and contexts:
                tasks.append(_eval_faithfulness())
            else:
                logger.debug(
                    "Skipping faithfulness for direct or empty context trace %s",
                    trace_id,
                )

        # Evaluate Answer Relevancy
        if "answer_relevancy" in settings.eval_metrics:
            tasks.append(_eval_answer_relevancy())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        timeout = settings.eval_timeout_seconds if settings.eval_timeout_seconds > 0 else None
        if timeout:
            await asyncio.wait_for(_evaluate(), timeout=timeout)
        else:
            await _evaluate()
    except asyncio.TimeoutError:
        logger.warning(
            "Online evaluation timed out after %.1fs for trace %s",
            settings.eval_timeout_seconds,
            trace_id,
        )
    except Exception as exc:
        logger.warning(
            "Unexpected error during online evaluation for trace %s: %s",
            trace_id,
            exc,
        )

