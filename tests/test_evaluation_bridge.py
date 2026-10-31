from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.rag.evaluation import EvaluationEngine


def test_score_trace_metric_submits_to_langfuse():
    """Mock Langfuse client, call engine.score_metric("tr-1", "faithfulness", 0.95, "looks good"),
    verify client.create_score called with exact arguments and returns True."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()

    engine = EvaluationEngine(client=mock_client)

    result = engine.score_metric("tr-1", "faithfulness", 0.95, "looks good")
    assert result is True
    mock_client.create_score.assert_called_once_with(
        trace_id="tr-1",
        name="faithfulness",
        value=0.95,
        comment="looks good",
    )


def test_score_trace_metric_handles_disabled_telemetry(monkeypatch):
    """Set client to None, call engine.score_metric(), assert returns False and no exception raised."""
    engine = EvaluationEngine(client=None)
    monkeypatch.setattr("app.core.telemetry.get_langfuse_client", lambda: None)

    result = engine.score_metric("tr-disabled", "faithfulness", 0.95, "looks good")
    assert result is False


def test_score_trace_metric_handles_langfuse_error():
    """Mock client.score raising network exception, assert function catches error and returns False."""
    mock_client = MagicMock(spec=["score"])
    mock_client.score.side_effect = RuntimeError("Network connection failed")

    engine = EvaluationEngine(client=mock_client)

    result = engine.score_metric("tr-err", "faithfulness", 0.95, "error test")
    assert result is False


def test_score_trace_metric_handles_create_score_error():
    """Mock client.create_score raising exception, assert function catches error and returns False."""
    mock_client = MagicMock()
    mock_client.create_score.side_effect = ConnectionError("Langfuse unreachable")

    engine = EvaluationEngine(client=mock_client)

    result = engine.score_metric("tr-err2", "faithfulness", 0.8)
    assert result is False


def test_score_trace_metric_falls_back_to_score_method():
    """Verify fallback to client.score when client does not implement create_score."""
    mock_client = MagicMock(spec=["score"])
    mock_client.score = MagicMock()

    engine = EvaluationEngine(client=mock_client)

    result = engine.score_metric("tr-fallback", "faithfulness", 0.85, "fallback test")
    assert result is True
    mock_client.score.assert_called_once_with(
        trace_id="tr-fallback",
        name="faithfulness",
        value=0.85,
        comment="fallback test",
    )


def test_score_trace_metric_handles_client_without_scoring_methods():
    """If client has neither create_score nor score, returns False."""
    mock_client = MagicMock(spec=[])

    engine = EvaluationEngine(client=mock_client)

    result = engine.score_metric("tr-no-method", "faithfulness", 0.5)
    assert result is False


def test_score_trace_metric_normalizes_scores():
    """Scores submitted to Langfuse must be strictly normalized between 0.0 and 1.0."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()

    engine = EvaluationEngine(client=mock_client)

    # Score above 1.0 clamped to 1.0
    engine.score_metric("tr-norm1", "metric", 1.5)
    mock_client.create_score.assert_called_with(
        trace_id="tr-norm1",
        name="metric",
        value=1.0,
        comment=None,
    )

    # Score below 0.0 clamped to 0.0
    engine.score_metric("tr-norm2", "metric", -0.3)
    mock_client.create_score.assert_called_with(
        trace_id="tr-norm2",
        name="metric",
        value=0.0,
        comment=None,
    )


def test_score_trace_faithfulness_disabled_telemetry():
    """If Langfuse is disabled/None, evaluate returns None for faithfulness without raising."""
    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=False),
        client=None,
    )

    res = engine.evaluate(
        trace_id="tr-disabled",
        question="What is the WiFi password?",
        answer="The password is guest123.",
        source_used="kb",
        retrieved_contexts=["Guest WiFi password is guest123."],
    )
    assert res.faithfulness is None


def test_score_trace_faithfulness_success():
    """Verify evaluate calculates faithfulness via judge and reports score to Langfuse."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()
    mock_judge = MagicMock()
    mock_judge.llm = MagicMock()

    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=True, eval_sample_rate=1.0, eval_metrics=["faithfulness"]),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    class MockFaithfulnessMetric:
        def __init__(self, **kwargs):
            self.score = 0.92
            self.reason = "All claims supported by context."

        def measure(self, test_case, _show_indicator=False):
            assert test_case.input == "What is the WiFi password?"
            assert test_case.actual_output == "The password is guest123."
            assert test_case.retrieval_context == ["Guest WiFi password is guest123."]
            return self.score

    with patch("deepeval.metrics.FaithfulnessMetric", MockFaithfulnessMetric):
        res = engine.evaluate(
            trace_id="tr-faith-1",
            question="What is the WiFi password?",
            answer="The password is guest123.",
            source_used="kb",
            retrieved_contexts=["Guest WiFi password is guest123."],
        )
    assert res.faithfulness == 0.92
    mock_client.create_score.assert_called_once_with(
        trace_id="tr-faith-1",
        name="faithfulness",
        value=0.92,
        comment="All claims supported by context.",
    )


def test_score_trace_faithfulness_handles_judge_error():
    """If judge evaluation fails or times out, the error is logged and returns None (fail-open)."""
    mock_client = MagicMock()
    mock_judge = MagicMock()
    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=True, eval_sample_rate=1.0, eval_metrics=["faithfulness"]),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    class FailingMetric:
        def __init__(self, **kwargs):
            pass

        def measure(self, test_case, _show_indicator=False):
            raise TimeoutError("Evaluation LLM timeout after 30s")

    with patch("deepeval.metrics.FaithfulnessMetric", FailingMetric):
        res = engine.evaluate(
            trace_id="tr-err-eval",
            question="How do I setup VPN?",
            answer="Follow the instructions.",
            source_used="kb",
            retrieved_contexts=["VPN guide."],
        )
    assert res.faithfulness is None


def test_score_trace_faithfulness_fails_when_metric_submission_fails():
    """If scoring submission to Langfuse fails, evaluate logs warning and retains result."""
    mock_client = MagicMock()
    mock_judge = MagicMock()
    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=True, eval_sample_rate=1.0, eval_metrics=["faithfulness"]),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    class MockFaithfulnessMetric:
        def __init__(self, **kwargs):
            self.score = 0.8
            self.reason = "Good"

        def measure(self, test_case, _show_indicator=False):
            return self.score

    with patch("deepeval.metrics.FaithfulnessMetric", MockFaithfulnessMetric), patch.object(
        engine, "score_metric", return_value=False
    ) as mock_score:
        res = engine.evaluate(
            trace_id="tr-fail-sub",
            question="How do I reset password?",
            answer="Go to settings.",
            source_used="kb",
            retrieved_contexts=["Password reset instructions."],
        )
    mock_score.assert_called_once()
    assert res.faithfulness == 0.8


def test_score_trace_answer_relevancy_success():
    """Verify evaluate calculates relevancy and reports to Langfuse."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()
    mock_judge = MagicMock()
    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=True, eval_sample_rate=1.0, eval_metrics=["answer_relevancy"]),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    class MockRelevancyMetric:
        def __init__(self, **kwargs):
            self.score = 0.95
            self.reason = "Highly relevant response."

        def measure(self, test_case, _show_indicator=False):
            assert test_case.input == "What is MFA?"
            assert test_case.actual_output == "MFA is multi-factor authentication."
            return self.score

    with patch("deepeval.metrics.AnswerRelevancyMetric", MockRelevancyMetric):
        res = engine.evaluate(
            trace_id="tr-rel-1",
            question="What is MFA?",
            answer="MFA is multi-factor authentication.",
            source_used="direct",
            retrieved_contexts=[],
        )
    assert res.answer_relevancy == 0.95
    mock_client.create_score.assert_called_once_with(
        trace_id="tr-rel-1",
        name="answer_relevancy",
        value=0.95,
        comment="Highly relevant response.",
    )


def test_score_trace_answer_relevancy_disabled_telemetry():
    """If Langfuse client is None, answer relevancy returns None without raising."""
    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=False),
        client=None,
    )

    res = engine.evaluate(
        trace_id="tr-disabled-rel",
        question="What is MFA?",
        answer="MFA is multi-factor authentication.",
        source_used="direct",
        retrieved_contexts=[],
    )
    assert res.answer_relevancy is None


def test_score_trace_answer_relevancy_handles_judge_error():
    """If answer relevancy evaluation throws, returns None."""
    mock_client = MagicMock()
    mock_judge = MagicMock()
    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=True, eval_sample_rate=1.0, eval_metrics=["answer_relevancy"]),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    class FailingMetric:
        def __init__(self, **kwargs):
            pass

        def measure(self, test_case, _show_indicator=False):
            raise RuntimeError("Answer relevancy evaluation failed")

    with patch("deepeval.metrics.AnswerRelevancyMetric", FailingMetric):
        res = engine.evaluate(
            trace_id="tr-err-rel",
            question="What is MFA?",
            answer="MFA is multi-factor authentication.",
            source_used="direct",
            retrieved_contexts=[],
        )
    assert res.answer_relevancy is None
