# test_example.py
from deepeval import assert_test
from deepeval.test_case import LLMTestCase, SingleTurnParams
from deepeval.metrics import GEval
from deepeval.models import LocalModel
from src import config

def test_correctness():
    
    custom_judge = LocalModel(
        model=config.MODEL, 
        base_url=config.BASE_URL,  
        api_key=config.API_KEY.get_secret_value(),           
    )
    
    correctness_metric = GEval(
        name="Correctness",
        criteria=(
            "Determine if the 'actual output' is factually correct based on the 'expected output'. "
            "Ignore differences in phrasing, conversational filler, or formatting as long as the core value/answer matches."
        ),
        evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
        model=custom_judge,
        threshold=0.5
    )
    
    test_case = LLMTestCase(
        input="What is 5 / 2?",
        actual_output="The result is 2.5.",
        expected_output="2.5"
    )
    assert_test(test_case, [correctness_metric])