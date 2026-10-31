import asyncio
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.rag.evaluation import EvaluationEngine, FakeEvaluator
from app.rag.workflows.runner import get_workflow_runner


@pytest.fixture
def base_eval_settings():
    return Settings(
        langfuse_enabled=True,
        eval_sample_rate=1.0,
        eval_metrics=["faithfulness", "answer_relevancy"],
        eval_timeout_seconds=5.0,
    )


@pytest.mark.asyncio
class TestSamplingGate:
    """Verify deterministic sampling gate logic in EvaluationEngine."""

    async def test_eval_skipped_when_sample_rate_zero(self, base_eval_settings):
        base_eval_settings.eval_sample_rate = 0.0
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockFaith:
            def __init__(self, **kwargs):
                self.score = 0.9

            def measure(self, tc, _show_indicator=False):
                return self.score

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.95

            def measure(self, tc, _show_indicator=False):
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", MockFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", MockRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-001",
                question="What is X?",
                answer="X is Y",
                source_used="kb",
                retrieved_contexts=["ctx 1"],
            )

        # Metric calculated offline, but no dispatch to Langfuse
        assert res.faithfulness == 0.9
        assert res.answer_relevancy == 0.95
        assert mock_client.create_score.call_count == 0

    async def test_eval_skipped_when_random_ge_sample_rate(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_sample_rate = 0.4
        monkeypatch.setattr("random.random", lambda: 0.40)
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockFaith:
            def __init__(self, **kwargs):
                self.score = 0.9

            def measure(self, tc, _show_indicator=False):
                return self.score

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.95

            def measure(self, tc, _show_indicator=False):
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", MockFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", MockRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-002",
                question="What is X?",
                answer="X is Y",
                source_used="kb",
                retrieved_contexts=["ctx 1"],
            )

        assert res.faithfulness == 0.9
        assert res.answer_relevancy == 0.95
        assert mock_client.create_score.call_count == 0

    async def test_eval_runs_when_random_lt_sample_rate(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_sample_rate = 0.5
        monkeypatch.setattr("random.random", lambda: 0.35)
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockFaith:
            def __init__(self, **kwargs):
                self.score = 0.9

            def measure(self, tc, _show_indicator=False):
                return self.score

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.95

            def measure(self, tc, _show_indicator=False):
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", MockFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", MockRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-003",
                question="What is X?",
                answer="X is Y",
                source_used="kb",
                retrieved_contexts=["ctx 1"],
            )

        assert res.faithfulness == 0.9
        assert res.answer_relevancy == 0.95
        assert mock_client.create_score.call_count == 2

    async def test_eval_skipped_when_langfuse_disabled(self, base_eval_settings):
        base_eval_settings.langfuse_enabled = False
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockFaith:
            def __init__(self, **kwargs):
                self.score = 0.9

            def measure(self, tc, _show_indicator=False):
                return self.score

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.95

            def measure(self, tc, _show_indicator=False):
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", MockFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", MockRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-004",
                question="What is X?",
                answer="X is Y",
                source_used="kb",
                retrieved_contexts=["ctx 1"],
            )

        # Computes metrics offline, but skips telemetry dispatch
        assert res.faithfulness == 0.9
        assert res.answer_relevancy == 0.95
        assert mock_client.create_score.call_count == 0

    async def test_eval_skipped_when_empty_inputs(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        res = await engine.aevaluate(
            trace_id="trace-empty",
            question="",
            answer="X is Y",
            source_used="kb",
            retrieved_contexts=["ctx 1"],
        )

        assert res.faithfulness is None
        assert res.answer_relevancy is None
        assert mock_client.create_score.call_count == 0


@pytest.mark.asyncio
class TestDirectAndContextRules:
    """Verify faithfulness is skipped when source_used == direct or retrieved_contexts is empty."""

    async def test_direct_answer_skips_faithfulness_and_runs_relevancy(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.98

            def measure(self, tc, _show_indicator=False):
                assert tc.input == "Hello, how are you?"
                assert tc.actual_output == "I am an IT Assistant."
                return self.score

        with patch("deepeval.metrics.AnswerRelevancyMetric", MockRel):
            res = await engine.aevaluate(
                trace_id="trace-direct-001",
                question="Hello, how are you?",
                answer="I am an IT Assistant.",
                source_used="direct",
                retrieved_contexts=["some leftover context"],
            )

        assert res.faithfulness is None
        assert res.answer_relevancy == 0.98
        mock_client.create_score.assert_called_once_with(
            trace_id="trace-direct-001",
            name="answer_relevancy",
            value=0.98,
            comment="Evaluated via DeepEval LocalModel judge",
        )

    async def test_empty_contexts_skips_faithfulness_and_runs_relevancy(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.88

            def measure(self, tc, _show_indicator=False):
                return self.score

        with patch("deepeval.metrics.AnswerRelevancyMetric", MockRel):
            res = await engine.aevaluate(
                trace_id="trace-empty-ctx-001",
                question="What is VPN?",
                answer="VPN is Virtual Private Network.",
                source_used="kb",
                retrieved_contexts=[],
            )

        assert res.faithfulness is None
        assert res.answer_relevancy == 0.88

    async def test_none_contexts_skips_faithfulness_and_runs_relevancy(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.85

            def measure(self, tc, _show_indicator=False):
                return self.score

        with patch("deepeval.metrics.AnswerRelevancyMetric", MockRel):
            res = await engine.aevaluate(
                trace_id="trace-none-ctx-001",
                question="What is VPN?",
                answer="VPN is Virtual Private Network.",
                source_used="web",
                retrieved_contexts=None,
            )

        assert res.faithfulness is None
        assert res.answer_relevancy == 0.85


@pytest.mark.asyncio
class TestKBAndWebGroundedEvaluation:
    """Verify both faithfulness and answer relevancy run when retrieval produces contexts."""

    async def test_kb_grounded_answer_runs_both_metrics(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        contexts = ["KB chunk 1: VPN instructions", "KB chunk 2: Port configuration"]

        class MockFaith:
            def __init__(self, **kwargs):
                self.score = 0.92

            def measure(self, tc, _show_indicator=False):
                assert tc.input == "How do I set up VPN?"
                assert tc.actual_output == "Follow chunk 1 and 2 instructions."
                assert tc.retrieval_context == contexts
                return self.score

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.96

            def measure(self, tc, _show_indicator=False):
                assert tc.input == "How do I set up VPN?"
                assert tc.actual_output == "Follow chunk 1 and 2 instructions."
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", MockFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", MockRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-kb-001",
                question="How do I set up VPN?",
                answer="Follow chunk 1 and 2 instructions.",
                source_used="kb",
                retrieved_contexts=contexts,
            )

        assert res.faithfulness == 0.92
        assert res.answer_relevancy == 0.96
        assert mock_client.create_score.call_count == 2

    async def test_web_grounded_answer_runs_both_metrics(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockFaith:
            def __init__(self, **kwargs):
                self.score = 0.89

            def measure(self, tc, _show_indicator=False):
                return self.score

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.94

            def measure(self, tc, _show_indicator=False):
                return self.score

        contexts = ["Web snippet: Error code 0x800"]
        with (
            patch("deepeval.metrics.FaithfulnessMetric", MockFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", MockRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-web-001",
                question="Fix 0x800",
                answer="Restart machine.",
                source_used="web",
                retrieved_contexts=contexts,
            )

        assert res.faithfulness == 0.89
        assert res.answer_relevancy == 0.94
        assert mock_client.create_score.call_count == 2

    async def test_eval_metrics_config_filtering_faithfulness_only(self, base_eval_settings):
        base_eval_settings.eval_metrics = ["faithfulness"]
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockFaith:
            def __init__(self, **kwargs):
                self.score = 0.95

            def measure(self, tc, _show_indicator=False):
                return self.score

        with patch("deepeval.metrics.FaithfulnessMetric", MockFaith):
            res = await engine.aevaluate(
                trace_id="trace-faith-only",
                question="Q?",
                answer="A",
                source_used="kb",
                retrieved_contexts=["ctx"],
            )

        assert res.faithfulness == 0.95
        assert res.answer_relevancy is None
        assert mock_client.create_score.call_count == 1

    async def test_eval_metrics_config_filtering_relevancy_only(self, base_eval_settings):
        base_eval_settings.eval_metrics = ["answer_relevancy"]
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class MockRel:
            def __init__(self, **kwargs):
                self.score = 0.91

            def measure(self, tc, _show_indicator=False):
                return self.score

        with patch("deepeval.metrics.AnswerRelevancyMetric", MockRel):
            res = await engine.aevaluate(
                trace_id="trace-rel-only",
                question="Q?",
                answer="A",
                source_used="kb",
                retrieved_contexts=["ctx"],
            )

        assert res.faithfulness is None
        assert res.answer_relevancy == 0.91
        assert mock_client.create_score.call_count == 1


@pytest.mark.asyncio
class TestTimeoutAndFailOpenSafety:
    """Verify evaluator gracefully catches timeouts and judge exceptions without raising."""

    async def test_eval_timeout_fails_open(self, base_eval_settings):
        base_eval_settings.eval_timeout_seconds = 0.05
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class SlowFaith:
            def __init__(self, **kwargs):
                self.score = 1.0

            def measure(self, tc, _show_indicator=False):
                time.sleep(0.2)
                return self.score

        class FastRel:
            def __init__(self, **kwargs):
                self.score = 0.9

            def measure(self, tc, _show_indicator=False):
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", SlowFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", FastRel),
        ):
            # Must not raise asyncio.TimeoutError
            res = await engine.aevaluate(
                trace_id="trace-timeout-001",
                question="Slow question",
                answer="Slow answer",
                source_used="kb",
                retrieved_contexts=["ctx"],
            )
        assert res.faithfulness is None

    async def test_eval_faithfulness_exception_fails_open(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class FailingFaith:
            def __init__(self, **kwargs):
                pass

            def measure(self, tc, _show_indicator=False):
                raise RuntimeError("Judge LLM API unavailable")

        class GoodRel:
            def __init__(self, **kwargs):
                self.score = 0.90

            def measure(self, tc, _show_indicator=False):
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", FailingFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", GoodRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-fail-001",
                question="Fault question",
                answer="Fault answer",
                source_used="kb",
                retrieved_contexts=["ctx"],
            )

        assert res.faithfulness is None
        assert res.answer_relevancy == 0.90

    async def test_eval_relevancy_exception_fails_open(self, base_eval_settings):
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        class GoodFaith:
            def __init__(self, **kwargs):
                self.score = 0.88

            def measure(self, tc, _show_indicator=False):
                return self.score

        class FailingRel:
            def __init__(self, **kwargs):
                pass

            def measure(self, tc, _show_indicator=False):
                raise ValueError("DeepEval parser failure")

        with (
            patch("deepeval.metrics.FaithfulnessMetric", GoodFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", FailingRel),
        ):
            res = await engine.aevaluate(
                trace_id="trace-fail-002",
                question="Fault question 2",
                answer="Fault answer 2",
                source_used="kb",
                retrieved_contexts=["ctx"],
            )

        assert res.faithfulness == 0.88
        assert res.answer_relevancy is None

    async def test_default_eval_timeout_is_120s(self):
        s = Settings()
        assert s.eval_timeout_seconds == 120.0

    async def test_eval_metrics_run_concurrently(self, base_eval_settings):
        """Verify faithfulness and answer relevancy execute concurrently in threadpool."""
        mock_client = MagicMock()
        mock_judge = MagicMock()
        engine = EvaluationEngine(
            settings=base_eval_settings,
            judge_provider=lambda: mock_judge,
            client=mock_client,
        )

        started = set()
        both_running = False

        class SlowFaith:
            def __init__(self, **kwargs):
                self.score = 0.9

            def measure(self, tc, _show_indicator=False):
                nonlocal both_running
                started.add("faithfulness")
                time.sleep(0.05)
                if "relevancy" in started:
                    both_running = True
                return self.score

        class SlowRel:
            def __init__(self, **kwargs):
                self.score = 0.95

            def measure(self, tc, _show_indicator=False):
                nonlocal both_running
                started.add("relevancy")
                time.sleep(0.05)
                if "faithfulness" in started:
                    both_running = True
                return self.score

        with (
            patch("deepeval.metrics.FaithfulnessMetric", SlowFaith),
            patch("deepeval.metrics.AnswerRelevancyMetric", SlowRel),
        ):
            t0 = time.perf_counter()
            await engine.aevaluate(
                trace_id="trace-concurrent-001",
                question="Q",
                answer="A",
                source_used="kb",
                retrieved_contexts=["ctx"],
            )
            elapsed = time.perf_counter() - t0

        assert both_running, "Both evaluation tasks should overlap in execution"
        assert elapsed < 0.2, f"Concurrent execution should be fast, took {elapsed}s"


class TestRouteIntegration:
    """Verify POST /api/chat and POST /api/chat/stream dispatch background evaluation via Evaluator seam."""

    def test_sync_chat_enqueues_background_evaluation(self, register_mock_workflow):
        client = TestClient(app)
        fake_evaluator = FakeEvaluator()
        runner = get_workflow_runner()
        runner.evaluator = fake_evaluator
        runner.trace_extractor = lambda handler: "trace-sync-123"

        register_mock_workflow(
            "fix_single_llm",
            answer="Test sync answer",
            source_used="kb",
            retrieved_contexts=["doc chunk 1", "doc chunk 2"],
            trace=["retrieve_kb", "generate_from_kb"],
            citations=[],
        )

        payload = {
            "question": "How to resolve sync issue?",
            "workflow": "fix_single_llm",
        }
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "Test sync answer"

        assert len(fake_evaluator.evaluate_calls) == 1
        call = fake_evaluator.evaluate_calls[0]
        assert call["trace_id"] == "trace-sync-123"
        assert call["question"] == "How to resolve sync issue?"
        assert call["answer"] == "Test sync answer"
        assert call["source_used"] == "kb"
        assert call["retrieved_contexts"] == ["doc chunk 1", "doc chunk 2"]

    @pytest.mark.asyncio
    async def test_streaming_chat_triggers_background_evaluation(self, register_mock_workflow):
        eval_event = asyncio.Event()
        captured_call = {}

        class RecordingEvaluator(FakeEvaluator):
            async def aevaluate(
                self,
                question: str,
                answer: str,
                source_used: str,
                retrieved_contexts: list[str],
                trace_id: str | None = None,
            ):
                captured_call.update(
                    {
                        "question": question,
                        "answer": answer,
                        "source_used": source_used,
                        "retrieved_contexts": retrieved_contexts,
                        "trace_id": trace_id,
                    }
                )
                eval_event.set()
                return await super().aevaluate(question, answer, source_used, retrieved_contexts, trace_id)

        evaluator = RecordingEvaluator()
        runner = get_workflow_runner()
        runner.evaluator = evaluator
        runner.trace_extractor = lambda handler: "trace-stream-456"

        async def mock_astream_events(*args, **kwargs):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "retrieve_kb"}, "data": {}}
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "retrieve_kb"},
                "data": {
                    "output": {
                        "retrieved_contexts": ["Stream doc chunk A"],
                        "trace": ["retrieve_kb"],
                    }
                },
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {"chunk": {"content": "Streamed "}},
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {"chunk": {"content": "response token"}},
            }
            yield {
                "event": "on_chain_end",
                "metadata": {"langgraph_node": "generate_from_kb"},
                "data": {
                    "output": {
                        "answer": "Streamed response token",
                        "source_used": "kb",
                        "citations": [],
                        "retrieved_contexts": ["Stream doc chunk A"],
                    }
                },
            }

        mock_graph = MagicMock()
        mock_graph.astream_events = mock_astream_events
        register_mock_workflow("fix_single_llm", graph=mock_graph)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Stream question?", "workflow": "fix_single_llm"},
            ) as resp:
                assert resp.status_code == 200
                _ = [line async for line in resp.aiter_lines()]

        # Wait briefly for background task
        await asyncio.wait_for(eval_event.wait(), timeout=2.0)
        assert captured_call["trace_id"] == "trace-stream-456"
        assert captured_call["question"] == "Stream question?"
        assert captured_call["answer"] == "Streamed response token"
        assert captured_call["source_used"] == "kb"
        assert captured_call["retrieved_contexts"] == ["Stream doc chunk A"]
