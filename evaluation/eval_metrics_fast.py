import json
import logging
from pathlib import Path
from typing import Any

from deepeval import evaluate
from deepeval.evaluate import AsyncConfig, CacheConfig, ErrorConfig
from deepeval.metrics import (
    AnswerRelevancyMetric,
     BaseMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.test_case import LLMTestCase, SingleTurnParams
from evaluation.judge import judge

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).resolve().parent
INPUT_PATH = EVAL_DIR / "data" / "results.jsonl"
OUTPUT_PATH = EVAL_DIR / "data" / "evaluated_results.jsonl"

# Map DeepEval metric names to output JSONL keys
METRIC_KEY_MAP: dict[str, str] = {
    "answer relevancy": "answer_relevancy",
    "faithfulness": "faithfulness",
    "contextual relevancy": "context_relevancy",
    "correctness": "correctness",
}


def normalize_context(context: Any) -> list[Any]:
    """Safely convert various context inputs into a list of strings for DeepEval."""
    if context is None:
        return []
    if isinstance(context, str):
        return [context]
    if isinstance(context, list):
        return [str(item) for item in context if item is not None]
    return [str(context)]


def build_metrics(model: Any, threshold: float = 0.7) -> list[BaseMetric]:
    """Initialize and configure evaluation metrics."""
    return [
        AnswerRelevancyMetric(threshold=threshold, model=model),
        FaithfulnessMetric(threshold=threshold, model=model),
        ContextualRelevancyMetric(threshold=threshold, model=model),
        GEval(
            name="Correctness",
            criteria="Determine whether the actual output is factually correct based on the expected output",
            evaluation_params=[
                SingleTurnParams.ACTUAL_OUTPUT,
                SingleTurnParams.EXPECTED_OUTPUT,
            ],
            threshold=threshold,
            model=model,
        ),
    ]


def load_test_cases(input_path: Path) -> tuple[list[dict[str, Any]], list[LLMTestCase]]:
    """Load input JSONL file and return raw records alongside LLMTestCase objects."""
    records: list[dict[str, Any]] = []
    test_cases: list[LLMTestCase] = []

    with input_path.open("r", encoding="utf-8") as file:
        for line_num, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping invalid JSON at line {line_num}: {e}")
                continue

            test_case = LLMTestCase(
                input=record.get("question", ""),
                actual_output=record.get("actual_answer", ""),
                expected_output=record.get("expected_answer", ""),
                retrieval_context=normalize_context(record.get("context")),
            )
            records.append(record)
            test_cases.append(test_case)

    return records, test_cases


def resolve_metric_key(raw_name: str) -> str:
    """Normalize metric names (including GEval suffixes) to standard output keys."""
    cleaned = raw_name.strip().lower()
    for prefix, key in METRIC_KEY_MAP.items():
        if cleaned.startswith(prefix):
            return key
    return cleaned.replace(" ", "_")


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")

    records, test_cases = load_test_cases(INPUT_PATH)
    logger.info(f"Loaded {len(test_cases)} test cases for evaluation.")

    metrics = build_metrics(model=judge.llm, threshold=0.7)

    evaluation_results = evaluate(
        test_cases=test_cases,
        metrics=metrics,
        # Prevent a single failing test from crashing the entire evaluation run
        error_config=ErrorConfig(ignore_errors=True),
        # Limit concurrent requests to avoid LLM API rate limits
        async_config=AsyncConfig(run_async=True, max_concurrent=5),
        cache_config=CacheConfig(use_cache=False, write_cache=False),
    )

    # Sort test_results by their original index if DeepEval provides the 'order' attribute
    sorted_test_results = sorted(
        evaluation_results.test_results,
        key=lambda r: (getattr(r, "order", None) is None, getattr(r, "order", 0)),
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as out_file:
        for original_record, test_result in zip(records, sorted_test_results):
            scores: dict[str, float | None] = {
                key: None for key in METRIC_KEY_MAP.values()
            }
            reasons: dict[str, str | None] = {}

            for metric in test_result.metrics_data or []:
                key = resolve_metric_key(metric.name)
                scores[key] = metric.score
                reasons[f"{key}_reason"] = metric.reason

            evaluated_result = {
                **original_record,
                **scores,
                **reasons,
            }
            out_file.write(json.dumps(evaluated_result, ensure_ascii=False) + "\n")

    logger.info(f"Done! Evaluated results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()