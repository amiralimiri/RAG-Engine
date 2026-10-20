import json
from pathlib import Path
from src.agent import ask
from tqdm import tqdm

EVAL_DIR = Path(__file__).resolve().parent
INPUT_PATH = EVAL_DIR / "eval_dataset.jsonl"
OUTPUT_PATH = EVAL_DIR / "results.jsonl"

with INPUT_PATH.open("r", encoding="utf-8") as file:
    test_cases = [json.loads(line) for line in file if line.strip()]

print(f"Total test cases: {len(test_cases)}")

with OUTPUT_PATH.open("w", encoding="utf-8") as out_file:
    for i, test_case in enumerate(tqdm(test_cases, desc="Evaluating"), start=1):
        question = test_case.get("question", "")
        expected_answer = test_case.get("expected_answer", "")

        try:
            rag_result = ask(question)
            actual_answer = rag_result.get("answer", "")
            context = rag_result.get("context", "")
            status = "success"
        except Exception as e:
            tqdm.write(f"[Error at item {i}]: {e}")
            actual_answer = None
            context = None
            status = f"error: {str(e)}"
        
        result = {
            "index": i,
            "question": question,
            "expected_answer": expected_answer,
            "actual_answer": actual_answer,
            "context": context,
            "status": status,
        }
        
        out_file.write(json.dumps(result, ensure_ascii=False) + "\n")
        out_file.flush()

print(f"\nDone! Results saved to {OUTPUT_PATH}")