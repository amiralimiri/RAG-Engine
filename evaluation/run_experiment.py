"""Langfuse Dataset & Experimentation Runner for RAG Workflows.

Usage:
    uv run python evaluation/run_experiment.py [--dataset-name it-support-benchmark] [--workflows fix_single_llm,fix_single_jev]
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path when executed directly as a script
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from app.api.schemas import ChatRequest
from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.runner import WorkflowRunner, get_workflow_runner
from evaluation.judge import judge

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("experiment_runner")
settings = get_settings()

DEFAULT_BENCHMARK_PATH = Path("data/gold_benchmarks.json")
DEFAULT_DATASET_NAME = "it-support-benchmark"


def load_gold_benchmarks(benchmark_file: Path | str | None = None) -> list[dict[str, Any]]:
    """Loads canonical benchmark test cases from JSON file."""
    path = Path(benchmark_file) if benchmark_file else DEFAULT_BENCHMARK_PATH
    if not path.exists():
        raise FileNotFoundError(f"Missing benchmark file: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ensure_dataset(
    client: Any,
    dataset_name: str = DEFAULT_DATASET_NAME,
    benchmark_path: Path | str | None = None,
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
    benchmark_path: Path | str | None = None,
) -> Any:
    """Convenience alias for seeding or ensuring the golden benchmark dataset."""
    return ensure_dataset(client, dataset_name, benchmark_path)


def run_workflow_benchmark(
    workflow_name: str,
    dataset: Any = None,
    client: Any = None,
    run_timestamp: int | None = None,
    runner: WorkflowRunner | None = None,
) -> dict[str, Any]:
    """Runs a single workflow against all benchmark items in the dataset."""
    ts = run_timestamp or int(time.time())
    run_name = f"{workflow_name}-run-{ts}"
    logger.info("=== Running Experiment for Workflow: %s (%s) ===", workflow_name, run_name)

    active_runner = runner or get_workflow_runner()
    try:
        wf_def = active_runner.registry.get(workflow_name)
    except KeyError:
        raise ValueError(
            f"Workflow '{workflow_name}' is not registered in the active WorkflowRegistry. "
            f"Available workflows: {active_runner.registry.list_ids()}"
        )

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
        req = ChatRequest(question=question, workflow=workflow_name)
        chat_resp, final_state = active_runner._execute_with_state(req)
        latency = time.perf_counter() - start_t
        eval_ctx = wf_def.extract_eval_context(final_state) if wf_def is not None else None
        answer = chat_resp.answer or ""
        source_used = (eval_ctx.source_used if eval_ctx else None) or chat_resp.source_used or ""
        contexts = (eval_ctx.retrieved_contexts if eval_ctx else None) or []
        trace_id = chat_resp.trace_id

        total_latencies.append(latency)

        # Faithfulness metric (only if retrieved contexts exist and source is not direct)
        faith_val = None
        if source_used != "direct" and contexts and answer:
            try:
                tc = LLMTestCase(input=question, actual_output=answer, retrieval_context=contexts)
                fm = FaithfulnessMetric(threshold=0.5, model=judge.llm, async_mode=False)
                try:
                    fm.measure(tc, _show_indicator=False)
                except TypeError:
                    fm.measure(tc)
                faith_val = float(fm.score)
                faithfulness_scores.append(faith_val)
            except Exception as exc:
                logger.warning("Faithfulness calculation error for question '%s': %s", question[:30], exc)

        # Answer relevancy metric
        rel_val = None
        if answer:
            try:
                tc = LLMTestCase(input=question, actual_output=answer)
                arm = AnswerRelevancyMetric(threshold=0.5, model=judge.llm, async_mode=False)
                try:
                    arm.measure(tc, _show_indicator=False)
                except TypeError:
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


def main(argv: list[str] | None = None) -> list[dict[str, Any]]:
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

    available_ids = set(get_workflow_registry().list_ids())
    target_workflows = [w.strip() for w in args.workflows.split(",") if w.strip() in available_ids]
    if not target_workflows:
        logger.error("No valid workflows selected. Available: %s", sorted(available_ids))
        return []

    results_table = []
    run_timestamp = int(time.time())

    for wf_name in target_workflows:
        summary = run_workflow_benchmark(
            workflow_name=wf_name,
            dataset=dataset,
            client=client,
            run_timestamp=run_timestamp,
        )
        results_table.append(summary)

    print(format_summary_table(results_table))
    return results_table


if __name__ == "__main__":
    main()
