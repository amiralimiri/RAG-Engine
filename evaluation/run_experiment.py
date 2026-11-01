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

import yaml

# Ensure project root is in sys.path when executed directly as a script
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client
from app.rag.evaluation import Evaluator, get_evaluator
from app.rag.workflows.base import ExecutionProfile, WorkflowInput
from app.rag.workflows.runner import WorkflowRunner, get_workflow_runner

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
    evaluator: Evaluator | None = None,
    variant_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    profile: ExecutionProfile | None = None,
) -> dict[str, Any]:
    """Runs a single workflow against all benchmark items in the dataset."""
    ts = run_timestamp or int(time.time())
    active_runner = runner or get_workflow_runner()
    active_evaluator = evaluator or getattr(active_runner, "evaluator", None) or get_evaluator(client=client)
    try:
        wf_def = active_runner.registry.get(workflow_name)
    except KeyError:
        raise ValueError(
            f"Workflow '{workflow_name}' is not registered in the active WorkflowRegistry. "
            f"Available workflows: {active_runner.registry.list_ids()}"
        )

    if profile is not None:
        active_runner.registry.register_profile(workflow_name, profile)
        if not variant_id:
            variant_id = profile.variant_id

    effective_variant = variant_id or "default"
    run_name = f"{workflow_name}@{effective_variant}--{ts}"
    logger.info("=== Running Experiment for Workflow: %s (%s) ===", workflow_name, run_name)

    items = getattr(dataset, "items", dataset) if dataset else []
    total_latencies: list[float] = []
    faithfulness_scores: list[float] = []
    relevancy_scores: list[float] = []
    source_accuracy_scores: list[float] = []

    for item in items:
        if isinstance(item, dict):
            inp = item.get("input", "")
            question = inp.get("question", "") if isinstance(inp, dict) else str(inp)
            expected_output = item.get("expected_output")
            meta = item.get("metadata") or {}
            expected_source = item.get("expected_source") or (
                meta.get("expected_source") if isinstance(meta, dict) else None
            )
        else:
            inp = getattr(item, "input", "")
            question = inp.get("question", "") if isinstance(inp, dict) else str(inp)
            expected_output = getattr(item, "expected_output", None)
            meta = getattr(item, "metadata", None) or {}
            expected_source = (
                meta.get("expected_source") if isinstance(meta, dict) else None
            ) or getattr(item, "expected_source", None)

        start_t = time.perf_counter()
        wf_input = WorkflowInput(
            question=question,
            workflow=workflow_name,
            variant_id=variant_id,
            parameters=parameters or {},
        )
        exec_result = active_runner.execute_with_context(wf_input)
        latency = time.perf_counter() - start_t
        chat_resp = exec_result.response
        eval_ctx = exec_result.eval_context
        answer = chat_resp.answer or ""
        source_used = (eval_ctx.source_used if eval_ctx else None) or chat_resp.source_used or ""
        contexts = (eval_ctx.retrieved_contexts if eval_ctx else None) or []
        trace_id = chat_resp.trace_id

        total_latencies.append(latency)

        try:
            eval_res = active_evaluator.evaluate(
                question=question,
                answer=answer,
                source_used=source_used,
                retrieved_contexts=contexts,
                trace_id=trace_id,
                expected_output=expected_output,
                expected_source=expected_source,
            )
        except TypeError:
            eval_res = active_evaluator.evaluate(
                question=question,
                answer=answer,
                source_used=source_used,
                retrieved_contexts=contexts,
                trace_id=trace_id,
            )

        faith_val = eval_res.faithfulness
        if faith_val is not None:
            faithfulness_scores.append(faith_val)

        rel_val = eval_res.answer_relevancy
        if rel_val is not None:
            relevancy_scores.append(rel_val)

        src_val = eval_res.source_accuracy
        if src_val is None and hasattr(eval_res, "scores") and "source_accuracy" in eval_res.scores:
            src_val = eval_res.scores["source_accuracy"]
        if src_val is not None:
            source_accuracy_scores.append(src_val)

        # Link dataset item to experiment run if supported
        link_fn = getattr(item, "link", None)
        if callable(link_fn) and trace_id:
            try:
                link_fn(trace_or_observation=trace_id, run_name=run_name)
            except Exception as exc:
                logger.debug("Failed linking dataset item to run %s: %s", run_name, exc)

    items_count = len(items)
    avg_lat = sum(total_latencies) / len(total_latencies) if total_latencies else 0.0
    avg_faith = sum(faithfulness_scores) / len(faithfulness_scores) if faithfulness_scores else 0.0
    avg_rel = sum(relevancy_scores) / len(relevancy_scores) if relevancy_scores else 0.0
    avg_src = sum(source_accuracy_scores) / len(source_accuracy_scores) if source_accuracy_scores else 0.0

    return {
        "Workflow": workflow_name,
        "Variant": effective_variant,
        "Items Evaluated": items_count,
        "Avg Latency (s)": round(avg_lat, 3),
        "Avg Faithfulness": round(avg_faith, 3),
        "Avg Relevancy": round(avg_rel, 3),
        "Source Accuracy": round(avg_src, 3),
    }


def load_matrix_config(matrix_file: Path | str) -> list[dict[str, Any]]:
    """Loads and normalizes matrix configuration from a YAML file."""
    path = Path(matrix_file)
    if not path.exists():
        raise FileNotFoundError(f"Missing matrix configuration file: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        return []

    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict):
        if "matrix" in data and isinstance(data["matrix"], list):
            raw_entries = data["matrix"]
        elif "runs" in data and isinstance(data["runs"], list):
            raw_entries = data["runs"]
        elif "workflows" in data and isinstance(data["workflows"], list):
            raw_entries = []
            for wf in data["workflows"]:
                wf_id = wf.get("id") or wf.get("workflow") or wf.get("workflow_id")
                profiles = wf.get("profiles", [])
                if not profiles:
                    raw_entries.append({"workflow": wf_id, **wf})
                else:
                    for p in profiles:
                        entry = {**wf, **p, "workflow": wf_id}
                        entry.pop("profiles", None)
                        raw_entries.append(entry)
        else:
            raw_entries = [data]
    else:
        raise ValueError(f"Invalid YAML matrix format in {path}: expected list or mapping")

    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        wf = raw.get("workflow") or raw.get("workflow_id") or raw.get("id")
        if not wf:
            continue
        entry = {
            "workflow": str(wf),
            "variant_id": raw.get("variant_id") or raw.get("variant"),
            "model_name": raw.get("model_name"),
            "prompt_version": raw.get("prompt_version"),
            "temperature": float(raw["temperature"]) if raw.get("temperature") is not None else None,
            "parameters": raw.get("parameters") or {},
            "tags": raw.get("tags") or [],
            "metadata": raw.get("metadata") or {},
        }
        entries.append(entry)

    return entries


def run_matrix_benchmark(
    matrix_file: Path | str,
    dataset: Any = None,
    client: Any = None,
    run_timestamp: int | None = None,
    runner: WorkflowRunner | None = None,
    evaluator: Evaluator | None = None,
) -> list[dict[str, Any]]:
    """Runs matrix benchmarking across multiple workflow and execution profile configurations."""
    entries = load_matrix_config(matrix_file)
    if not entries:
        logger.warning("No valid entries found in matrix configuration: %s", matrix_file)
        return []

    active_runner = runner or get_workflow_runner()
    ts = run_timestamp or int(time.time())
    results_table: list[dict[str, Any]] = []

    for entry in entries:
        workflow_name = entry["workflow"]
        variant_id = entry.get("variant_id")

        profile = None
        if variant_id or entry.get("model_name") or entry.get("temperature") is not None or entry.get("prompt_version"):
            profile = ExecutionProfile(
                variant_id=variant_id or "default",
                model_name=entry.get("model_name"),
                prompt_version=entry.get("prompt_version"),
                temperature=entry.get("temperature"),
                parameters=entry.get("parameters") or {},
                tags=entry.get("tags") or [],
                metadata=entry.get("metadata") or {},
            )

        summary = run_workflow_benchmark(
            workflow_name=workflow_name,
            dataset=dataset,
            client=client,
            run_timestamp=ts,
            runner=active_runner,
            evaluator=evaluator,
            variant_id=variant_id,
            parameters=entry.get("parameters"),
            profile=profile,
        )
        results_table.append(summary)

    return results_table


def format_matrix_table(results_table: list[dict[str, Any]]) -> str:
    """Formats comparison matrix table displaying latency, faithfulness, relevancy, and source accuracy."""
    header = f"{'Workflow':<20} | {'Variant':<15} | {'Latency':<10} | {'Faithfulness':<12} | {'Relevancy':<10} | {'Source Acc':<10}"
    separator = "-" * len(header)
    rows = [header, separator]
    for r in results_table:
        wf = str(r.get("Workflow", ""))
        var = str(r.get("Variant", "default"))
        lat = f"{r.get('Avg Latency (s)', 0.0):.3f}s"
        faith = f"{r.get('Avg Faithfulness', 0.0):.3f}"
        rel = f"{r.get('Avg Relevancy', 0.0):.3f}"
        src_acc = f"{r.get('Source Accuracy', 0.0):.3f}"
        rows.append(f"{wf:<20} | {var:<15} | {lat:<10} | {faith:<12} | {rel:<10} | {src_acc:<10}")
    return "\n".join(rows)


def format_summary_table(results_table: list[dict[str, Any]]) -> str:
    """Formats experiment evaluation results into a formatted summary table string."""
    lines = [
        "",
        "=" * 85,
        "           LANGFUSE BENCHMARK EXPERIMENT RESULTS",
        "=" * 85,
    ]
    for r in results_table:
        variant_info = f" (Variant: {r['Variant']})" if "Variant" in r and r["Variant"] != "default" else ""
        lines.append(f"Workflow: {r['Workflow']}{variant_info}")
        lines.append(f"  - Avg Latency:       {r['Avg Latency (s)']}s")
        lines.append(f"  - Avg Faithfulness:  {r['Avg Faithfulness']}")
        lines.append(f"  - Avg Relevancy:     {r['Avg Relevancy']}")
        if "Source Accuracy" in r:
            lines.append(f"  - Source Accuracy:   {r['Source Accuracy']}")
        lines.append("-" * 85)

    if results_table:
        lines.append("")
        lines.append("COMPARISON MATRIX:")
        lines.append(format_matrix_table(results_table))
        lines.append("=" * 85)

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
        "--matrix",
        default=None,
        help="Path to YAML matrix benchmarking configuration file",
    )
    parser.add_argument(
        "--benchmark-file",
        default=str(DEFAULT_BENCHMARK_PATH),
        help="Path to gold benchmarks JSON file",
    )
    return parser


def main(
    argv: list[str] | None = None,
    runner: WorkflowRunner | None = None,
    evaluator: Evaluator | None = None,
) -> list[dict[str, Any]]:
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

    active_runner = runner or get_workflow_runner()
    run_timestamp = int(time.time())

    if args.matrix:
        results_table = run_matrix_benchmark(
            matrix_file=args.matrix,
            dataset=dataset,
            client=client,
            run_timestamp=run_timestamp,
            runner=active_runner,
            evaluator=evaluator,
        )
    else:
        available_ids = set(active_runner.registry.list_ids())
        target_workflows = [w.strip() for w in args.workflows.split(",") if w.strip() in available_ids]
        if not target_workflows:
            logger.error("No valid workflows selected. Available: %s", sorted(available_ids))
            return []

        results_table = []
        for wf_name in target_workflows:
            summary = run_workflow_benchmark(
                workflow_name=wf_name,
                dataset=dataset,
                client=client,
                run_timestamp=run_timestamp,
                runner=active_runner,
                evaluator=evaluator,
            )
            results_table.append(summary)

    print(format_summary_table(results_table))
    return results_table


if __name__ == "__main__":
    main()
