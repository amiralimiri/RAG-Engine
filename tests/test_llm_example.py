# test_example.py
from deepeval import assert_test
from deepeval.test_case import LLMTestCase, SingleTurnParams
from deepeval.metrics import GEval
from evaluation.judge import judge

def test_correctness():
     
    correctness_metric = GEval(
        name="Correctness",
        criteria=(
            "Determine if the 'actual output' is factually correct based on the 'expected output'. "
            "Ignore differences in phrasing, conversational filler, or formatting as long as the core value/answer matches."
        ),
        evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
        model=judge.llm,
        threshold=0.5
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