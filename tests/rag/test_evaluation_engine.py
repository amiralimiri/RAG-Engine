"""Unit tests for unified evaluation engine and Evaluator seam (Ticket 01)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.rag.evaluation.base import EvaluationResult, Evaluator
from app.rag.evaluation.evaluator import (
    EvaluationEngine,
    FakeEvaluator,
    get_evaluator,
    reset_evaluator,
)
from app.rag.evaluation.judge import JudgeModels


def test_evaluation_result_schema_defaults() -> None:
    """Verify EvaluationResult default field values and dict conversions."""
    res = EvaluationResult()
    assert res.faithfulness is None
    assert res.answer_relevancy is None
    assert res.scores == {}
    assert res.details == {}

    custom = EvaluationResult(
        faithfulness=0.92,
        answer_relevancy=0.88,
        scores={"faithfulness": 0.92, "answer_relevancy": 0.88},
        details={"comment": "High quality answer"},
    )
    assert custom.faithfulness == 0.92
    assert custom.answer_relevancy == 0.88
    assert custom.scores["faithfulness"] == 0.92
    assert custom.details["comment"] == "High quality answer"


def test_evaluator_protocol_runtime_checkable() -> None:
    """Verify both FakeEvaluator and EvaluationEngine satisfy Evaluator protocol."""
    fake = FakeEvaluator()
    assert isinstance(fake, Evaluator)

    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=False),
        judge_provider=lambda: MagicMock(),
        client=MagicMock(),
    )
    assert isinstance(engine, Evaluator)


def test_fake_evaluator_sync_and_async_execution() -> None:
    """Verify FakeEvaluator records calls and returns configured metrics synchronously and asynchronously."""
    fake = FakeEvaluator(faithfulness=0.95, answer_relevancy=0.85)

    # Synchronous evaluation
    res_sync = fake.evaluate(
        question="How to connect VPN?",
        answer="Use the Cisco AnyConnect client.",
        source_used="vectorstore",
        retrieved_contexts=["VPN connection guide"],
        trace_id="trace-123",
    )
    assert res_sync.faithfulness == 0.95
    assert res_sync.answer_relevancy == 0.85
    assert len(fake.evaluate_calls) == 1
    assert fake.evaluate_calls[0]["trace_id"] == "trace-123"

    # Direct score submission
    scored = fake.score_metric(trace_id="trace-123", metric_name="user_feedback", score=1.0, comment="Great")
    assert scored is True
    assert len(fake.score_calls) == 1
    assert fake.score_calls[0]["name"] == "user_feedback"
    assert fake.score_calls[0]["score"] == 1.0


@pytest.mark.asyncio
async def test_fake_evaluator_async_execution() -> None:
    """Verify FakeEvaluator asynchronously evaluates and records call history."""
    fake = FakeEvaluator(faithfulness=0.80, answer_relevancy=0.90)

    res_async = await fake.aevaluate(
        question="How to request access?",
        answer="Visit the self-service IT portal.",
        source_used="vectorstore",
        retrieved_contexts=["Access portal documentation"],
        trace_id="trace-async-1",
    )
    assert res_async.faithfulness == 0.80
    assert res_async.answer_relevancy == 0.90
    assert len(fake.evaluate_calls) == 1
    assert fake.evaluate_calls[0]["trace_id"] == "trace-async-1"


def test_judge_models_lazy_initialization() -> None:
    """Verify JudgeModels defers instantiation until properties are accessed."""
    with (
        patch("deepeval.models.LocalModel") as mock_local_model,
        patch("deepeval.models.TypeSafeModel") as mock_typesafe_model,
    ):
        judge = JudgeModels(settings=Settings(llm_model="test-model", jev_model="test-jev"))
        # Verify no models initialized on object creation
        assert mock_local_model.call_count == 0
        assert mock_typesafe_model.call_count == 0

        # Access LLM
        _ = judge.llm
        assert mock_local_model.call_count == 1
        assert mock_typesafe_model.call_count == 0

        # Access JEV
        _ = judge.jev
        assert mock_typesafe_model.call_count == 1


def test_evaluation_engine_sampling_gate() -> None:
    """Verify EvaluationEngine computes metrics offline but gates Langfuse score dispatch."""
    mock_client = MagicMock()
    mock_judge = MagicMock()
    mock_judge.llm = MagicMock()

    with (
        patch("deepeval.metrics.FaithfulnessMetric") as mock_fm_cls,
        patch("deepeval.metrics.AnswerRelevancyMetric") as mock_arm_cls,
    ):
        mock_fm = MagicMock()
        mock_fm.score = 0.95
        mock_fm.reason = "Faithful context"
        mock_fm_cls.return_value = mock_fm

        mock_arm = MagicMock()
        mock_arm.score = 0.90
        mock_arm.reason = "Highly relevant"
        mock_arm_cls.return_value = mock_arm

        # Disabled in settings: metrics computed, but no scores dispatched to Langfuse
        engine_disabled = EvaluationEngine(
            settings=Settings(
                langfuse_enabled=False,
                eval_metrics=["faithfulness", "answer_relevancy"],
            ),
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )
        res = engine_disabled.evaluate(
            question="Q", answer="A", source_used="vectorstore", retrieved_contexts=["ctx"], trace_id="tr-disabled"
        )
        assert res.faithfulness == 0.95
        assert res.answer_relevancy == 0.90
        assert mock_client.create_score.call_count == 0

        # Sample rate <= 0.0: metrics computed, but no scores dispatched
        engine_sampled_out = EvaluationEngine(
            settings=Settings(
                langfuse_enabled=True,
                eval_sample_rate=0.0,
                eval_metrics=["faithfulness", "answer_relevancy"],
            ),
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )
        res_sampled = engine_sampled_out.evaluate(
            question="Q", answer="A", source_used="vectorstore", retrieved_contexts=["ctx"], trace_id="tr-sampled"
        )
        assert res_sampled.faithfulness == 0.95
        assert res_sampled.answer_relevancy == 0.90
        assert mock_client.create_score.call_count == 0


def test_evaluation_engine_score_metric_delegation() -> None:
    """Verify score_metric normalizes scores and delegates to Langfuse client."""
    mock_client = MagicMock()
    engine = EvaluationEngine(
        settings=Settings(langfuse_enabled=True),
        client=mock_client,
    )

    # Score clamping between 0.0 and 1.0
    success = engine.score_metric(
        trace_id="test-trace",
        metric_name="faithfulness",
        score=1.5,  # Should clamp to 1.0
        comment="Test score",
    )
    assert success is True
    mock_client.create_score.assert_called_once_with(
        trace_id="test-trace",
        name="faithfulness",
        value=1.0,
        comment="Test score",
    )


def test_evaluator_singleton_factory_and_reset() -> None:
    """Verify get_evaluator returns singleton and reset_evaluator resets instance."""
    reset_evaluator()
    eval1 = get_evaluator()
    eval2 = get_evaluator()
    assert eval1 is eval2

    reset_evaluator()
    eval3 = get_evaluator()
    assert eval3 is not eval1

    # Override factory with custom instance
    custom_fake = FakeEvaluator()
    eval_override = get_evaluator(evaluator=custom_fake)
    assert eval_override is custom_fake


def test_evaluation_engine_metric_computation_and_scoring() -> None:
    """Verify EvaluationEngine computes metrics and pushes scores when trace_id is provided."""
    mock_client = MagicMock()
    mock_judge = MagicMock()
    mock_judge.llm = MagicMock()

    engine = EvaluationEngine(
        settings=Settings(
            langfuse_enabled=True,
            eval_sample_rate=1.0,
            eval_metrics=["faithfulness", "answer_relevancy"],
        ),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    with (
        patch("deepeval.metrics.FaithfulnessMetric") as mock_fm_cls,
        patch("deepeval.metrics.AnswerRelevancyMetric") as mock_arm_cls,
    ):
        mock_fm = MagicMock()
        mock_fm.score = 0.95
        mock_fm.reason = "Faithful context"
        mock_fm_cls.return_value = mock_fm

        mock_arm = MagicMock()
        mock_arm.score = 0.90
        mock_arm.reason = "Highly relevant"
        mock_arm_cls.return_value = mock_arm

        res = engine.evaluate(
            question="What is the password policy?",
            answer="Passwords must be at least 14 characters.",
            source_used="vectorstore",
            retrieved_contexts=["Password policy document requires 14 chars."],
            trace_id="test-trace-id",
        )

        assert res.faithfulness == 0.95
        assert res.answer_relevancy == 0.90
        assert res.scores["faithfulness"] == 0.95
        assert res.scores["answer_relevancy"] == 0.90
        assert res.details["faithfulness_reason"] == "Faithful context"

        # Verify scores were submitted to client
        assert mock_client.create_score.call_count == 2


def test_evaluation_engine_skips_faithfulness_for_direct_source() -> None:
    """Verify faithfulness is skipped when source_used is direct or contexts are empty."""
    mock_client = MagicMock()
    mock_judge = MagicMock()

    engine = EvaluationEngine(
        settings=Settings(
            langfuse_enabled=True,
            eval_sample_rate=1.0,
            eval_metrics=["faithfulness", "answer_relevancy"],
        ),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    with (
        patch("deepeval.metrics.FaithfulnessMetric") as mock_fm_cls,
        patch("deepeval.metrics.AnswerRelevancyMetric") as mock_arm_cls,
    ):
        mock_arm = MagicMock()
        mock_arm.score = 0.88
        mock_arm_cls.return_value = mock_arm

        res = engine.evaluate(
            question="Hello",
            answer="Hi there, how can I help you?",
            source_used="direct",
            retrieved_contexts=[],
        )

        assert res.faithfulness is None
        assert res.answer_relevancy == 0.88
        assert mock_fm_cls.call_count == 0
        assert mock_arm_cls.call_count == 1


@pytest.mark.asyncio
async def test_evaluation_engine_aevaluate_threadpool_offloading() -> None:
    """Verify aevaluate asynchronously computes metrics in threadpool."""
    mock_client = MagicMock()
    mock_judge = MagicMock()

    engine = EvaluationEngine(
        settings=Settings(
            langfuse_enabled=True,
            eval_sample_rate=1.0,
            eval_metrics=["answer_relevancy"],
        ),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    with patch("deepeval.metrics.AnswerRelevancyMetric") as mock_arm_cls:
        mock_arm = MagicMock()
        mock_arm.score = 0.92
        mock_arm_cls.return_value = mock_arm

        res = await engine.aevaluate(
            question="How do I get an access token?",
            answer="Use the OAuth2 token endpoint.",
            source_used="vectorstore",
            retrieved_contexts=["Tokens can be generated via OAuth."],
        )
        assert res.answer_relevancy == 0.92


def test_evaluation_engine_source_accuracy_and_expected_output() -> None:
    """Verify EvaluationEngine computes source_accuracy and stores expected_output."""
    mock_client = MagicMock()
    mock_judge = MagicMock()

    engine = EvaluationEngine(
        settings=Settings(
            langfuse_enabled=True,
            eval_sample_rate=1.0,
            eval_metrics=[],
        ),
        judge_provider=lambda: mock_judge,
        client=mock_client,
    )

    # Test match (case-insensitive)
    res_match = engine.evaluate(
        question="How to reset password?",
        answer="Click forgot password link.",
        source_used="vectorstore",
        retrieved_contexts=[],
        trace_id="tr-match-1",
        expected_output="Reset password instructions.",
        expected_source="VectorStore",
    )
    assert res_match.source_accuracy == 1.0
    assert res_match.scores["source_accuracy"] == 1.0
    assert res_match.reference_correctness is not None
    assert res_match.scores["reference_correctness"] == res_match.reference_correctness
    assert res_match.details["expected_source"] == "VectorStore"
    assert res_match.details["expected_output"] == "Reset password instructions."
    assert res_match.details["source_accuracy"] == 1.0
    assert mock_client.create_score.call_count == 2
    metric_names = [call.kwargs["name"] for call in mock_client.create_score.call_args_list]
    assert "source_accuracy" in metric_names
    assert "reference_correctness" in metric_names

    # Test mismatch
    mock_client.reset_mock()
    res_mismatch = engine.evaluate(
        question="How to reset password?",
        answer="I don't know.",
        source_used="direct",
        retrieved_contexts=[],
        trace_id="tr-mismatch-1",
        expected_output="Reset password instructions.",
        expected_source="vectorstore",
    )
    assert res_mismatch.source_accuracy == 0.0
    assert res_mismatch.scores["source_accuracy"] == 0.0
    assert res_mismatch.reference_correctness is not None
    assert mock_client.create_score.call_count == 2
    mismatch_names = [call.kwargs["name"] for call in mock_client.create_score.call_args_list]
    assert "source_accuracy" in mismatch_names
    assert "reference_correctness" in mismatch_names


def test_fake_evaluator_ground_truth_source_accuracy() -> None:
    """Verify FakeEvaluator computes source_accuracy based on expected_source and records score."""
    fake = FakeEvaluator()

    res = fake.evaluate(
        question="What is VPN?",
        answer="Virtual Private Network.",
        source_used="vectorstore",
        retrieved_contexts=[],
        trace_id="tr-fake-1",
        expected_output="VPN definition.",
        expected_source="vectorstore",
    )
    assert res.source_accuracy == 1.0
    assert res.scores["source_accuracy"] == 1.0
    assert res.reference_correctness is not None
    assert res.details["expected_source"] == "vectorstore"
    assert res.details["expected_output"] == "VPN definition."
    assert len(fake.score_calls) == 2
    metric_names = [call["name"] for call in fake.score_calls]
    assert "source_accuracy" in metric_names
    assert "reference_correctness" in metric_names
    assert fake.score_calls[0]["trace_id"] == "tr-fake-1"
    assert fake.evaluate_calls[0]["expected_source"] == "vectorstore"
    assert fake.evaluate_calls[0]["expected_output"] == "VPN definition."

    # Test mismatch
    res_mismatch = fake.evaluate(
        question="What is VPN?",
        answer="Something else.",
        source_used="web_search",
        retrieved_contexts=[],
        trace_id="tr-fake-2",
        expected_source="vectorstore",
    )
    assert res_mismatch.source_accuracy == 0.0
    assert res_mismatch.scores["source_accuracy"] == 0.0
    assert len(fake.score_calls) == 3
    assert fake.score_calls[2]["score"] == 0.0
