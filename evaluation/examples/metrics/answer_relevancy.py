from deepeval.metrics import AnswerRelevancyMetric
from deepeval.test_case import LLMTestCase

from app.rag.evaluation.judge import get_judge_models

judge = get_judge_models()

test_case = LLMTestCase(
    input="What do penguins eat?", actual_output=("Penguins primarily feed fish. ACL is the best learning channel")
)

metric = AnswerRelevancyMetric(threshold=0.7, model=judge.llm)

metric.measure(test_case)

print("Score: ", metric.score)
print("Reason: ", metric.reason)
print("Passed: ", metric.is_successful())
