import logging
from typing import Optional

from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from app.core import telemetry
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
    if not client:
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
    if not client:
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
