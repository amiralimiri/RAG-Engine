import json
from pathlib import Path
from statistics import mean
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
INPUT_PATH = EVAL_DIR / "data" / "evaluated_results.jsonl"


METRICS = [
    "answer_relevancy",
    "faithfulness",
    "context_relevancy",
    "correctness",
]

THRESHOLD = 0.7

def load_results(input_path: Path) -> list[dict[str, Any]]:
    """Load evaluated JSONL records safely, ignoring empty lines."""
    if not input_path.exists():
        raise FileNotFoundError(f"Evaluated file not found: {input_path}")

    with input_path.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]
    
    
def main() -> None:
    results = load_results(INPUT_PATH)
    total_records = len(results)

    if total_records == 0:
        print("No evaluation records found.")
        return

    print("\n================ RAG Evaluation Summary ================\n")

    for metric in METRICS:
        # Filter out None values caused by LLM judge timeouts or parsing errors
        valid_scores: list[float] = [
             float(result[metric])
            for result in results
            if result.get(metric) is not None
        ]

        errored_count = total_records - len(valid_scores)
        title = metric.replace("_", " ").title()

        print(f"--- {title} ---")

        if not valid_scores:
            print("No valid scores available (all tests failed/errored).\n")
            continue

        average_score = mean(valid_scores)
        passed = sum(score >= THRESHOLD for score in valid_scores)
        failed = len(valid_scores) - passed
        pass_rate = (passed / len(valid_scores)) * 100

        print(f"Average Score : {average_score:.2f}")
        print(f"Passed        : {passed}/{len(valid_scores)}")
        print(f"Failed        : {failed}/{len(valid_scores)}")
        if errored_count:
            print(f"Errors/Skipped: {errored_count}/{total_records}")
        print(f"Pass Rate     : {pass_rate:.1f}%\n")


if __name__ == "__main__":
    main()