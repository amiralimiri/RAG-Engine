from unittest.mock import MagicMock
import pytest

from app.services.evaluation import (
    score_trace_metric,
    score_trace_faithfulness,
    score_trace_answer_relevancy,
)


def test_score_trace_metric_submits_to_langfuse(monkeypatch):
    """Mock Langfuse client, call score_trace_metric("tr-1", "faithfulness", 0.95, "looks good"),
    verify client.create_score called with exact arguments and returns True."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()

    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    result = score_trace_metric("tr-1", "faithfulness", 0.95, "looks good")
    assert result is True
    mock_client.create_score.assert_called_once_with(
        trace_id="tr-1",
        name="faithfulness",
        value=0.95,
        comment="looks good",
    )


def test_score_trace_metric_handles_disabled_telemetry(monkeypatch):
    """Set client to None, call score_trace_metric(), assert returns False and no exception raised."""
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: None)

    result = score_trace_metric("tr-disabled", "faithfulness", 0.95, "looks good")
    assert result is False


def test_score_trace_metric_handles_langfuse_error(monkeypatch):
    """Mock client.score raising network exception, assert function catches error and returns False."""
    mock_client = MagicMock(spec=["score"])
    mock_client.score.side_effect = RuntimeError("Network connection failed")

    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    result = score_trace_metric("tr-err", "faithfulness", 0.95, "error test")
    assert result is False


def test_score_trace_metric_handles_create_score_error(monkeypatch):
    """Mock client.create_score raising exception, assert function catches error and returns False."""
    mock_client = MagicMock()
    mock_client.create_score.side_effect = ConnectionError("Langfuse unreachable")

    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    result = score_trace_metric("tr-err2", "faithfulness", 0.8)
    assert result is False


def test_score_trace_metric_falls_back_to_score_method(monkeypatch):
    """Verify fallback to client.score when client does not implement create_score."""
    mock_client = MagicMock(spec=["score"])
    mock_client.score = MagicMock()

    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    result = score_trace_metric("tr-fallback", "faithfulness", 0.85, "fallback test")
    assert result is True
    mock_client.score.assert_called_once_with(
        trace_id="tr-fallback",
        name="faithfulness",
        value=0.85,
        comment="fallback test",
    )


def test_score_trace_metric_handles_client_without_scoring_methods(monkeypatch):
    """If client has neither create_score nor score, returns False."""
    mock_client = MagicMock(spec=[])
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    result = score_trace_metric("tr-no-method", "faithfulness", 0.5)
    assert result is False


def test_score_trace_metric_normalizes_scores(monkeypatch):
    """Scores submitted to Langfuse must be strictly normalized between 0.0 and 1.0."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    # Score above 1.0 clamped to 1.0
    score_trace_metric("tr-norm1", "metric", 1.5)
    mock_client.create_score.assert_called_with(
        trace_id="tr-norm1",
        name="metric",
        value=1.0,
        comment=None,
    )

    # Score below 0.0 clamped to 0.0
    score_trace_metric("tr-norm2", "metric", -0.3)
    mock_client.create_score.assert_called_with(
        trace_id="tr-norm2",
        name="metric",
        value=0.0,
        comment=None,
    )


def test_score_trace_faithfulness_disabled_telemetry(monkeypatch):
    """If Langfuse is disabled/None, score_trace_faithfulness returns None without raising."""
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: None)

    score = score_trace_faithfulness(
        trace_id="tr-disabled",
        question="What is the WiFi password?",
        answer="The password is guest123.",
        retrieved_contexts=["Guest WiFi password is guest123."],
    )
    assert score is None


def test_score_trace_faithfulness_success(monkeypatch):
    """Verify score_trace_faithfulness calculates metric via judge and reports score to Langfuse."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    class MockFaithfulnessMetric:
        def __init__(self, **kwargs):
            self.score = 0.92
            self.reason = "All claims supported by context."

        def measure(self, test_case):
            assert test_case.input == "What is the WiFi password?"
            assert test_case.actual_output == "The password is guest123."
            assert test_case.retrieval_context == ["Guest WiFi password is guest123."]
            return self.score

    monkeypatch.setattr("app.services.evaluation.FaithfulnessMetric", MockFaithfulnessMetric)

    score = score_trace_faithfulness(
        trace_id="tr-faith-1",
        question="What is the WiFi password?",
        answer="The password is guest123.",
        retrieved_contexts=["Guest WiFi password is guest123."],
    )
    assert score == 0.92
    mock_client.create_score.assert_called_once_with(
        trace_id="tr-faith-1",
        name="faithfulness",
        value=0.92,
        comment="All claims supported by context.",
    )


def test_score_trace_faithfulness_handles_judge_error(monkeypatch):
    """If judge evaluation fails or times out, the error is logged and returns None (fail-open)."""
    mock_client = MagicMock()
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    class FailingMetric:
        def __init__(self, **kwargs):
            pass

        def measure(self, test_case):
            raise TimeoutError("Evaluation LLM timeout after 30s")

    monkeypatch.setattr("app.services.evaluation.FaithfulnessMetric", FailingMetric)

    score = score_trace_faithfulness(
        trace_id="tr-err-eval",
        question="How do I setup VPN?",
        answer="Follow the instructions.",
        retrieved_contexts=["VPN guide."],
    )
    assert score is None


def test_score_trace_faithfulness_fails_when_metric_submission_fails(monkeypatch):
    """If scoring submission to Langfuse fails, score_trace_faithfulness returns None."""
    mock_client = MagicMock()
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    class MockFaithfulnessMetric:
        def __init__(self, **kwargs):
            self.score = 0.8
            self.reason = "Good"

        def measure(self, test_case):
            return self.score

    monkeypatch.setattr("app.services.evaluation.FaithfulnessMetric", MockFaithfulnessMetric)
    monkeypatch.setattr("app.services.evaluation.score_trace_metric", lambda *args, **kwargs: False)

    score = score_trace_faithfulness(
        trace_id="tr-fail-sub",
        question="How do I reset password?",
        answer="Go to settings.",
        retrieved_contexts=["Password reset instructions."],
    )
    assert score is None


def test_score_trace_answer_relevancy_success(monkeypatch):
    """Verify score_trace_answer_relevancy calculates relevancy and reports to Langfuse."""
    mock_client = MagicMock()
    mock_client.create_score = MagicMock()
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    class MockRelevancyMetric:
        def __init__(self, **kwargs):
            self.score = 0.95
            self.reason = "Highly relevant response."

        def measure(self, test_case):
            assert test_case.input == "What is MFA?"
            assert test_case.actual_output == "MFA is multi-factor authentication."
            return self.score

    monkeypatch.setattr("app.services.evaluation.AnswerRelevancyMetric", MockRelevancyMetric)

    score = score_trace_answer_relevancy(
        trace_id="tr-rel-1",
        question="What is MFA?",
        answer="MFA is multi-factor authentication.",
    )
    assert score == 0.95
    mock_client.create_score.assert_called_once_with(
        trace_id="tr-rel-1",
        name="answer_relevancy",
        value=0.95,
        comment="Highly relevant response.",
    )


def test_score_trace_answer_relevancy_disabled_telemetry(monkeypatch):
    """If Langfuse client is None, answer relevancy returns None without raising."""
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: None)

    score = score_trace_answer_relevancy(
        trace_id="tr-disabled-rel",
        question="What is MFA?",
        answer="MFA is multi-factor authentication.",
    )
    assert score is None


def test_score_trace_answer_relevancy_handles_judge_error(monkeypatch):
    """If answer relevancy evaluation throws, returns None."""
    mock_client = MagicMock()
    monkeypatch.setattr("app.services.evaluation.get_langfuse_client", lambda: mock_client)

    class FailingMetric:
        def __init__(self, **kwargs):
            pass

        def measure(self, test_case):
            raise RuntimeError("Answer relevancy evaluation failed")

    monkeypatch.setattr("app.services.evaluation.AnswerRelevancyMetric", FailingMetric)

    score = score_trace_answer_relevancy(
        trace_id="tr-err-rel",
        question="What is MFA?",
        answer="MFA is multi-factor authentication.",
    )
    assert score is None

