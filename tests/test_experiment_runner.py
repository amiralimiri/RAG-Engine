import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.core.config import get_settings


def test_gold_benchmarks_file_exists_and_schema():
    """Verify data/gold_benchmarks.json exists, is valid JSON, and has all 10 canonical test cases."""
    benchmark_file = Path("data/gold_benchmarks.json")
    assert benchmark_file.exists(), "data/gold_benchmarks.json does not exist"

    with open(benchmark_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert isinstance(data, list), "gold_benchmarks.json must contain a JSON list"
    assert len(data) == 10, f"Expected exactly 10 benchmark items, found {len(data)}"

    required_keys = {"id", "input", "expected_output", "expected_route", "expected_source", "category"}
    expected_ids = [
        "tc-01-greeting",
        "tc-02-kb-vpn-pulse",
        "tc-03-kb-password-reset",
        "tc-04-kb-mfa-token",
        "tc-05-kb-guest-wifi",
        "tc-06-web-docker-error",
        "tc-07-web-python-uv",
        "tc-08-rewrite-printer-vague",
        "tc-09-insufficient-alien",
        "tc-10-direct-farewell",
    ]

    found_ids = []
    valid_routes = {"direct", "kb"}
    valid_sources = {"direct", "vectorstore", "web_search", "insufficient_evidence"}
    valid_categories = {
        "direct_conversation",
        "private_kb_retrieval",
        "web_fallback",
        "query_rewrite",
        "insufficient_evidence",
    }

    for item in data:
        assert isinstance(item, dict), f"Item must be a dictionary: {item}"
        missing = required_keys - set(item.keys())
        assert not missing, f"Item {item.get('id')} missing keys: {missing}"

        assert isinstance(item["id"], str) and item["id"].strip()
        assert isinstance(item["input"], str) and item["input"].strip()
        assert isinstance(item["expected_output"], str) and item["expected_output"].strip()
        assert item["expected_route"] in valid_routes, f"Invalid route: {item['expected_route']}"
        assert item["expected_source"] in valid_sources, f"Invalid source: {item['expected_source']}"
        assert item["category"] in valid_categories, f"Invalid category: {item['category']}"

        found_ids.append(item["id"])

    assert found_ids == expected_ids, f"Mismatch in canonical test case IDs: {found_ids} vs {expected_ids}"


def test_load_gold_benchmarks_helper():
    from evaluation.run_experiment import load_gold_benchmarks

    benchmarks = load_gold_benchmarks()
    assert isinstance(benchmarks, list)
    assert len(benchmarks) == 10
    assert benchmarks[0]["id"] == "tc-01-greeting"

    with pytest.raises(FileNotFoundError):
        load_gold_benchmarks(Path("data/non_existent_path.json"))


def test_ensure_dataset_when_dataset_exists():
    from evaluation.run_experiment import ensure_dataset

    mock_client = MagicMock()
    mock_dataset = MagicMock()
    mock_dataset.items = [MagicMock()]
    mock_client.get_dataset.return_value = mock_dataset

    dataset = ensure_dataset(mock_client, dataset_name="it-support-benchmark")

    assert dataset == mock_dataset
    mock_client.get_dataset.assert_called_once_with("it-support-benchmark")
    mock_client.create_dataset.assert_not_called()
    mock_client.create_dataset_item.assert_not_called()


def test_ensure_dataset_and_seed_when_dataset_not_found():
    from evaluation.run_experiment import ensure_dataset, seed_golden_dataset

    mock_client = MagicMock()
    mock_created_dataset = MagicMock()
    mock_created_dataset.items = []

    # First get_dataset raises Exception (not found), second returns mock_created_dataset
    mock_client.get_dataset.side_effect = [Exception("Dataset not found"), mock_created_dataset]

    dataset = ensure_dataset(mock_client, dataset_name="it-support-benchmark")

    assert dataset == mock_created_dataset
    mock_client.create_dataset.assert_called_once_with(
        name="it-support-benchmark",
        description="Enterprise IT Support Golden Benchmark",
    )
    assert mock_client.create_dataset_item.call_count == 10

    first_call_kwargs = mock_client.create_dataset_item.call_args_list[0].kwargs
    assert first_call_kwargs["dataset_name"] == "it-support-benchmark"
    assert first_call_kwargs["input"] == {"question": "Hello! Good morning, how can you help me today?"}
    assert first_call_kwargs["expected_output"] == "Warm greeting offering enterprise IT support assistance."
    assert first_call_kwargs["metadata"]["id"] == "tc-01-greeting"
    assert first_call_kwargs["metadata"]["expected_route"] == "direct"
    assert first_call_kwargs["metadata"]["expected_source"] == "direct"


def test_seed_golden_dataset_degrades_gracefully_when_offline():
    from evaluation.run_experiment import ensure_dataset, seed_golden_dataset

    # Client is None
    assert ensure_dataset(None, "it-support-benchmark") is None
    assert seed_golden_dataset(None, "it-support-benchmark") is None

    # Client raises error on creation
    mock_client = MagicMock()
    mock_client.get_dataset.side_effect = Exception("Not found")
    mock_client.create_dataset.side_effect = ConnectionError("Langfuse unreachable")

    res = seed_golden_dataset(mock_client, "it-support-benchmark")
    assert res is None


def test_run_benchmark_workflow_execution_and_metrics():
    from evaluation.run_experiment import run_workflow_benchmark

    mock_client = MagicMock()
    mock_dataset = MagicMock()

    mock_item = MagicMock()
    mock_item.input = {"question": "How do I configure Pulse Secure VPN on my corporate MacBook?"}
    mock_item.link = MagicMock()
    mock_dataset.items = [mock_item]

    mock_workflow = MagicMock(return_value={
        "answer": "Install Pulse Secure from Self Service portal.",
        "source_used": "vectorstore",
        "retrieved_contexts": ["Pulse Secure installation guide."],
        "trace_id": "tr-test-123",
    })

    with patch("evaluation.run_experiment.FaithfulnessMetric") as mock_fm_cls, \
         patch("evaluation.run_experiment.AnswerRelevancyMetric") as mock_arm_cls:
        
        mock_fm = MagicMock()
        mock_fm.score = 0.95
        mock_fm_cls.return_value = mock_fm

        mock_arm = MagicMock()
        mock_arm.score = 0.88
        mock_arm_cls.return_value = mock_arm

        summary = run_workflow_benchmark(
            workflow_name="fix_single_llm",
            workflow_fn=mock_workflow,
            dataset=mock_dataset,
            client=mock_client,
        )

        assert summary["Workflow"] == "fix_single_llm"
        assert summary["Items Evaluated"] == 1
        assert summary["Avg Faithfulness"] == 0.95
        assert summary["Avg Relevancy"] == 0.88
        assert summary["Avg Latency (s)"] >= 0.0

        mock_fm.measure.assert_called_once()
        mock_arm.measure.assert_called_once()
        assert mock_client.create_score.call_count == 2
        mock_item.link.assert_called_once()


def test_run_benchmark_skips_faithfulness_for_direct_source():
    from evaluation.run_experiment import run_workflow_benchmark

    mock_client = MagicMock()
    mock_dataset = MagicMock()

    mock_item = MagicMock()
    mock_item.input = {"question": "Hello!"}
    mock_item.link = MagicMock()
    mock_dataset.items = [mock_item]

    mock_workflow = MagicMock(return_value={
        "answer": "Hello, how can I assist you?",
        "source_used": "direct",
        "retrieved_contexts": [],
        "trace_id": "tr-direct-001",
    })

    with patch("evaluation.run_experiment.FaithfulnessMetric") as mock_fm_cls, \
         patch("evaluation.run_experiment.AnswerRelevancyMetric") as mock_arm_cls:

        mock_arm = MagicMock()
        mock_arm.score = 0.92
        mock_arm_cls.return_value = mock_arm

        summary = run_workflow_benchmark(
            workflow_name="fix_single_llm",
            workflow_fn=mock_workflow,
            dataset=mock_dataset,
            client=mock_client,
        )

        mock_fm_cls.assert_not_called()
        mock_arm.measure.assert_called_once()
        assert summary["Avg Faithfulness"] == 0.0
        assert summary["Avg Relevancy"] == 0.92


def test_format_summary_table():
    from evaluation.run_experiment import format_summary_table

    sample_results = [
        {
            "Workflow": "fix_single_llm",
            "Items Evaluated": 10,
            "Avg Latency (s)": 1.234,
            "Avg Faithfulness": 0.92,
            "Avg Relevancy": 0.88,
        },
        {
            "Workflow": "fix_single_jev",
            "Items Evaluated": 10,
            "Avg Latency (s)": 0.852,
            "Avg Faithfulness": 0.95,
            "Avg Relevancy": 0.91,
        },
    ]

    table_str = format_summary_table(sample_results)
    assert "LANGFUSE BENCHMARK EXPERIMENT RESULTS" in table_str
    assert "fix_single_llm" in table_str
    assert "fix_single_jev" in table_str
    assert "1.234s" in table_str
    assert "0.852s" in table_str
    assert "0.92" in table_str
    assert "0.95" in table_str


def test_arg_parser_configuration():
    from evaluation.run_experiment import build_arg_parser

    parser = build_arg_parser()
    args = parser.parse_args(["--dataset-name", "custom-benchmark", "--workflows", "fix_single_llm"])
    assert args.dataset_name == "custom-benchmark"
    assert args.workflows == "fix_single_llm"


def test_main_cli_execution_with_fallback(capsys):
    from evaluation.run_experiment import main

    mock_wf = MagicMock(return_value={
        "answer": "Test answer",
        "source_used": "vectorstore",
        "retrieved_contexts": ["Test context"],
        "trace_id": "test-tr-1",
    })

    with patch("evaluation.run_experiment.get_langfuse_client", return_value=None), \
         patch.dict("evaluation.run_experiment.WORKFLOWS", {"fix_single_llm": mock_wf}), \
         patch("evaluation.run_experiment.load_gold_benchmarks", return_value=[
             {"id": "tc-01-greeting", "input": "Hello", "expected_output": "Hi", "expected_route": "direct", "expected_source": "direct", "category": "direct_conversation"}
         ]), \
         patch("evaluation.run_experiment.FaithfulnessMetric") as mock_fm_cls, \
         patch("evaluation.run_experiment.AnswerRelevancyMetric") as mock_arm_cls:

        mock_fm = MagicMock()
        mock_fm.score = 0.95
        mock_fm_cls.return_value = mock_fm

        mock_arm = MagicMock()
        mock_arm.score = 0.90
        mock_arm_cls.return_value = mock_arm

        results = main(["--workflows", "fix_single_llm"])
        assert len(results) == 1
        assert results[0]["Workflow"] == "fix_single_llm"
        assert results[0]["Items Evaluated"] == 1
        assert results[0]["Avg Faithfulness"] == 0.95
        assert results[0]["Avg Relevancy"] == 0.90

        captured = capsys.readouterr()
        assert "LANGFUSE BENCHMARK EXPERIMENT RESULTS" in captured.out


def test_main_cli_invalid_workflow():
    from evaluation.run_experiment import main

    results = main(["--workflows", "nonexistent_workflow"])
    assert results == []

