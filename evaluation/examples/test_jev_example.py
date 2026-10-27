# test_example.py
from deepeval.metrics.jev_eval import Noul, Score, Choice
from deepeval.test_case import LLMTestCase, SingleTurnParams
from deepeval.metrics import JevEval
from deepeval import assert_test
from evaluation.judge import judge


def test_correctness():
        
    correctness_metric = JevEval(
        name="Correctness",
        evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
        questions=[

            # Noul: binary proposition, returns P(true)
            Noul("No fact in actual_output contradicts a fact in expected_output.", weight=2),

            # Score: ordered levels (worst first), returns expected level position normalized to 0-1
            Score(
                "How much of the detail in expected_output is present in actual_output?",
                levels=["Almost none", "Some", "Most", "All of it"],
            ),

            # Choice (example): unordered option set with credits; None option drops the question if P(None) >= 0.5
            Choice(
                "Compared to expected_output, how accurate is the actual_output?",
                options={
                    "Fully correct and conveys the exact same answer as expected_output": 1.0,
                    "Partially correct, contains some truth but misses key parts or has minor errors": 0.5,
                    "Completely wrong or contradicts expected_output": 0.0,
                    "Not applicable": None,
                },
            )
        ],
        system_one_model=judge.jev,
        threshold=0.5,
    )
    test_case = LLMTestCase(
        input="What is 5 / 2?",
        actual_output="The result is 2.5.",
        expected_output="2.5"
    )
    try:
        assert_test(test_case, [correctness_metric])
    except Exception as exc:
        if any(term in type(exc).__name__ for term in ("Timeout", "Connect", "RetryError")):
            import pytest
            pytest.skip(f"Live judge API unavailable: {exc}")
        raise