import json
from pathlib import Path
from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    ContextualRelevancyMetric,
    GEval,
)
from deepeval.test_case import LLMTestCase, SingleTurnParams
from evaluation.judge import judge


EVAL_DIR = Path(__file__).resolve().parent
INPUT_PATH = EVAL_DIR / "data/results.jsonl"
OUTPUT_PATH = EVAL_DIR / "data/evaluated_results.jsonl"

with INPUT_PATH.open("r", encoding="utf-8") as file:
    results = [json.loads(line) for line in file if line.strip()]

answer_relevancy = AnswerRelevancyMetric(threshold=0.7, model=judge.llm)
faithfulness = FaithfulnessMetric(threshold=0.7, model=judge.llm)
context_relevancy = ContextualRelevancyMetric(threshold=0.7, model=judge.llm)
correctness = GEval(
    name="Correctness",
    criteria="Determine whether the actual output is factually correct based on the expected output",
    evaluation_params=[
        SingleTurnParams.ACTUAL_OUTPUT,
        SingleTurnParams.EXPECTED_OUTPUT,
    ],
    model=judge.llm
)

with OUTPUT_PATH.open("w", encoding="utf-8") as out_file:
    for i, result in enumerate(results, start=1):
        print(f"\nEvaluating test {i}/{len(results)}")

        test_case = LLMTestCase(
            input=result["question"],
            actual_output=result["actual_answer"],
            expected_output=result["expected_answer"],
            retrieval_context=[result["context"]],
        )

        try:
            answer_relevancy.measure(test_case)
            faithfulness.measure(test_case)
            context_relevancy.measure(test_case)
            correctness.measure(test_case)

            evaluated_result = {
                **result,
                "answer_relevancy": answer_relevancy.score,
                "faithfulness": faithfulness.score,
                "context_relvancy": context_relevancy.score,
                "correctness": correctness.score,
            }

            out_file.write(json.dumps(evaluated_result, ensure_ascii=False) + "\n")
            out_file.flush()

            print("Answer Relevancy: ", answer_relevancy.score)
            print("faithfulness: ", faithfulness.score)
            print("context_relvancy: ", context_relevancy.score)
            print("correctness: ", correctness.score)

        except Exception as e:
            print(f"Error on test {i}: {e}")
            continue

print(f"\nDone! Results saved to {OUTPUT_PATH}")