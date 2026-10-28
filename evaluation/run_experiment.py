"""Langfuse Dataset & Experimentation Runner for RAG Workflows.

Usage:
    uv run python evaluation/run_experiment.py [--dataset-name it-support-benchmark] [--workflows fix_single_llm,fix_single_jev]
"""
import argparse
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any, Callable, Optional

# Ensure project root is in sys.path when executed directly as a script
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client
from app.rag import fix_single_jev, fix_single_llm
from evaluation.judge import judge

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("experiment_runner")
settings = get_settings()

WORKFLOWS: dict[str, Callable[[str], dict[str, Any]]] = {
    "fix_single_llm": fix_single_llm.ask,
    "fix_single_jev": fix_single_jev.ask,
}

DEFAULT_BENCHMARK_PATH = Path("data/gold_benchmarks.json")
DEFAULT_DATASET_NAME = "it-support-benchmark"


def load_gold_benchmarks(benchmark_file: Optional[Path | str] = None) -> list[dict[str, Any]]:
    """Loads canonical benchmark test cases from JSON file."""
    path = Path(benchmark_file) if benchmark_file else DEFAULT_BENCHMARK_PATH
    if not path.exists():
        raise FileNotFoundError(f"Missing benchmark file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dataset(
    client: Any,
    dataset_name: str = DEFAULT_DATASET_NAME,
    benchmark_path: Optional[Path | str] = None,
) -> Any:
    """Ensures that the benchmark dataset exists in Langfuse.
    
    If not found, creates the dataset and seeds the benchmark cases.
    Degrades gracefully if client is None or disconnected.
    """
    if client is None:
        logger.warning("Langfuse client is not available. Dataset verification skipped.")
        return None

    try:
        return client.get_dataset(dataset_name)
    except Exception:
        logger.info("Dataset '%s' not found in Langfuse. Seeding from gold benchmarks...", dataset_name)
        try:
            client.create_dataset(
                name=dataset_name,
                description="Enterprise IT Support Golden Benchmark",
            )
            benchmarks = load_gold_benchmarks(benchmark_path)
            for item in benchmarks:
                client.create_dataset_item(
                    dataset_name=dataset_name,
                    input={"question": item["input"]},
                    expected_output=item["expected_output"],
                    metadata={
                        "id": item["id"],
                        "expected_route": item["expected_route"],
                        "expected_source": item["expected_source"],
                        "category": item["category"],
                    },
                )
            logger.info("Successfully seeded %d items into Langfuse dataset '%s'", len(benchmarks), dataset_name)
            return client.get_dataset(dataset_name)
        except Exception as exc:
            logger.warning("Failed to create or seed Langfuse dataset '%s': %s", dataset_name, exc)
            return None


def seed_golden_dataset(
    client: Any = None,
    dataset_name: str = DEFAULT_DATASET_NAME,
    benchmark_path: Optional[Path | str] = None,
) -> Any:
    """Convenience alias for seeding or ensuring the golden benchmark dataset."""
    return ensure_dataset(client, dataset_name, benchmark_path)


def run_workflow_benchmark(
    workflow_name: str,
    workflow_fn: Callable[[str], dict[str, Any]],
    dataset: Any,
    client: Any = None,
    run_timestamp: Optional[int] = None,
) -> dict[str, Any]:
    """Runs a single workflow against all benchmark items in the dataset."""
    ts = run_timestamp or int(time.time())
    run_name = f"{workflow_name}-run-{ts}"
    logger.info("=== Running Experiment for Workflow: %s (%s) ===", workflow_name, run_name)

    items = getattr(dataset, "items", dataset) if dataset else []
    total_latencies: list[float] = []
    faithfulness_scores: list[float] = []
    relevancy_scores: list[float] = []

    for item in items:
        if isinstance(item, dict):
            inp = item.get("input", "")
            question = inp.get("question", "") if isinstance(inp, dict) else str(inp)
        else:
            inp = getattr(item, "input", "")
            question = inp.get("question", "") if isinstance(inp, dict) else str(inp)

        start_t = time.perf_counter()
        result = workflow_fn(question)
        latency = time.perf_counter() - start_t
        total_latencies.append(latency)

        answer = result.get("answer", "")
        source_used = result.get("source_used", "")
        contexts = result.get("retrieved_contexts", [])
        trace_id = result.get("trace_id")

        # Faithfulness metric (only if retrieved contexts exist and source is not direct)
        faith_val = None
        if source_used != "direct" and contexts:
            try:
                tc = LLMTestCase(input=question, actual_output=answer, retrieval_context=contexts)
                fm = FaithfulnessMetric(threshold=0.5, model=judge.llm, async_mode=False)
                fm.measure(tc)
                faith_val = float(fm.score)
                faithfulness_scores.append(faith_val)
            except Exception as exc:
                logger.warning("Faithfulness calculation error for question '%s': %s", question[:30], exc)

        # Answer relevancy metric
        rel_val = None
        try:
            tc = LLMTestCase(input=question, actual_output=answer)
            arm = AnswerRelevancyMetric(threshold=0.5, model=judge.llm, async_mode=False)
            arm.measure(tc)
            rel_val = float(arm.score)
            relevancy_scores.append(rel_val)
        except Exception as exc:
            logger.warning("Answer relevancy calculation error for question '%s': %s", question[:30], exc)

        # Submit evaluation scores to Langfuse trace if available
        if client and trace_id:
            score_func = getattr(client, "create_score", None) or getattr(client, "score", None)
            if score_func and callable(score_func):
                if faith_val is not None:
                    try:
                        score_func(trace_id=trace_id, name="faithfulness", value=faith_val)
                    except Exception as exc:
                        logger.warning("Failed to score faithfulness on trace %s: %s", trace_id, exc)
                if rel_val is not None:
                    try:
                        score_func(trace_id=trace_id, name="answer_relevancy", value=rel_val)
                    except Exception as exc:
                        logger.warning("Failed to score answer_relevancy on trace %s: %s", trace_id, exc)

        # Link dataset item to experiment run if supported
        if hasattr(item, "link") and callable(getattr(item, "link")) and trace_id:
            try:
                item.link(trace_or_observation=trace_id, run_name=run_name)
            except Exception as exc:
                logger.debug("Failed linking dataset item to run %s: %s", run_name, exc)

    items_count = len(items)
    avg_lat = sum(total_latencies) / len(total_latencies) if total_latencies else 0.0
    avg_faith = sum(faithfulness_scores) / len(faithfulness_scores) if faithfulness_scores else 0.0
    avg_rel = sum(relevancy_scores) / len(relevancy_scores) if relevancy_scores else 0.0

    return {
        "Workflow": workflow_name,
        "Items Evaluated": items_count,
        "Avg Latency (s)": round(avg_lat, 3),
        "Avg Faithfulness": round(avg_faith, 3),
        "Avg Relevancy": round(avg_rel, 3),
    }


def format_summary_table(results_table: list[dict[str, Any]]) -> str:
    """Formats experiment evaluation results into a formatted summary table string."""
    lines = [
        "",
        "=" * 65,
        "           LANGFUSE BENCHMARK EXPERIMENT RESULTS",
        "=" * 65,
    ]
    for r in results_table:
        lines.append(f"Workflow: {r['Workflow']}")
        lines.append(f"  - Avg Latency:       {r['Avg Latency (s)']}s")
        lines.append(f"  - Avg Faithfulness:  {r['Avg Faithfulness']}")
        lines.append(f"  - Avg Relevancy:     {r['Avg Relevancy']}")
        lines.append("-" * 65)

    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds command-line argument parser for experiment runner."""
    parser = argparse.ArgumentParser(description="Run Langfuse Experimentation Benchmarks")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME, help="Langfuse dataset name")
    parser.add_argument(
        "--workflows",
        default="fix_single_llm,fix_single_jev",
        help="Comma-separated list of workflow names to benchmark",
    )
    parser.add_argument(
        "--benchmark-file",
        default=str(DEFAULT_BENCHMARK_PATH),
        help="Path to gold benchmarks JSON file",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """CLI entry point for running Langfuse benchmark experiments."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    client = get_langfuse_client()
    dataset = None
    if client:
        dataset = ensure_dataset(client, args.dataset_name, args.benchmark_file)

    if not dataset:
        logger.warning(
            "Langfuse dataset '%s' unavailable. Falling back to local benchmark file: %s",
            args.dataset_name,
            args.benchmark_file,
        )
        dataset = load_gold_benchmarks(args.benchmark_file)

    target_workflows = [w.strip() for w in args.workflows.split(",") if w.strip() in WORKFLOWS]
    if not target_workflows:
        logger.error("No valid workflows selected. Available: %s", list(WORKFLOWS.keys()))
        return []

    results_table = []
    run_timestamp = int(time.time())

    for wf_name in target_workflows:
        wf_fn = WORKFLOWS[wf_name]
        summary = run_workflow_benchmark(
            workflow_name=wf_name,
            workflow_fn=wf_fn,
            dataset=dataset,
            client=client,
            run_timestamp=run_timestamp,
        )
        results_table.append(summary)

    print(format_summary_table(results_table))
    return results_table


if __name__ == "__main__":
    main()
