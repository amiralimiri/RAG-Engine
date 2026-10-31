import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from starlette.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services.evaluation import run_online_evaluation_task


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
    """Verify deterministic sampling gate logic in run_online_evaluation_task."""

    async def test_eval_skipped_when_sample_rate_zero(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_sample_rate = 0.0
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock()
        mock_rel = MagicMock()
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-001",
            question="What is X?",
            answer="X is Y",
            source_used="kb",
            retrieved_contexts=["ctx 1"],
        )

        mock_faith.assert_not_called()
        mock_rel.assert_not_called()

    async def test_eval_skipped_when_random_ge_sample_rate(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_sample_rate = 0.4
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)
        monkeypatch.setattr("random.random", lambda: 0.40)

        mock_faith = MagicMock()
        mock_rel = MagicMock()
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-002",
            question="What is X?",
            answer="X is Y",
            source_used="kb",
            retrieved_contexts=["ctx 1"],
        )

        mock_faith.assert_not_called()
        mock_rel.assert_not_called()

    async def test_eval_runs_when_random_lt_sample_rate(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_sample_rate = 0.5
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)
        monkeypatch.setattr("random.random", lambda: 0.35)

        mock_faith = MagicMock(return_value=0.9)
        mock_rel = MagicMock(return_value=0.95)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-003",
            question="What is X?",
            answer="X is Y",
            source_used="kb",
            retrieved_contexts=["ctx 1"],
        )

        mock_faith.assert_called_once()
        mock_rel.assert_called_once()

    async def test_eval_skipped_when_langfuse_disabled(self, base_eval_settings, monkeypatch):
        base_eval_settings.langfuse_enabled = False
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock()
        mock_rel = MagicMock()
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-004",
            question="What is X?",
            answer="X is Y",
            source_used="kb",
            retrieved_contexts=["ctx 1"],
        )

        mock_faith.assert_not_called()
        mock_rel.assert_not_called()

    async def test_eval_skipped_when_trace_id_missing(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock()
        mock_rel = MagicMock()
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id=None,
            question="What is X?",
            answer="X is Y",
            source_used="kb",
            retrieved_contexts=["ctx 1"],
        )

        mock_faith.assert_not_called()
        mock_rel.assert_not_called()


@pytest.mark.asyncio
class TestDirectAndContextRules:
    """Verify faithfulness is skipped when source_used == direct or retrieved_contexts is empty."""

    async def test_direct_answer_skips_faithfulness_and_runs_relevancy(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock()
        mock_rel = MagicMock(return_value=0.98)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-direct-001",
            question="Hello, how are you?",
            answer="I am an IT Assistant.",
            source_used="direct",
            retrieved_contexts=["some leftover context"],
        )

        mock_faith.assert_not_called()
        mock_rel.assert_called_once_with(
            trace_id="trace-direct-001",
            question="Hello, how are you?",
            answer="I am an IT Assistant.",
        )

    async def test_empty_contexts_skips_faithfulness_and_runs_relevancy(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock()
        mock_rel = MagicMock(return_value=0.88)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-empty-ctx-001",
            question="What is VPN?",
            answer="VPN is Virtual Private Network.",
            source_used="kb",
            retrieved_contexts=[],
        )

        mock_faith.assert_not_called()
        mock_rel.assert_called_once()

    async def test_none_contexts_skips_faithfulness_and_runs_relevancy(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock()
        mock_rel = MagicMock(return_value=0.85)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-none-ctx-001",
            question="What is VPN?",
            answer="VPN is Virtual Private Network.",
            source_used="web",
            retrieved_contexts=None,
        )

        mock_faith.assert_not_called()
        mock_rel.assert_called_once()


@pytest.mark.asyncio
class TestKBAndWebGroundedEvaluation:
    """Verify both faithfulness and answer relevancy run when retrieval produces contexts."""

    async def test_kb_grounded_answer_runs_both_metrics(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock(return_value=0.92)
        mock_rel = MagicMock(return_value=0.96)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        contexts = ["KB chunk 1: VPN instructions", "KB chunk 2: Port configuration"]
        await run_online_evaluation_task(
            trace_id="trace-kb-001",
            question="How do I set up VPN?",
            answer="Follow chunk 1 and 2 instructions.",
            source_used="kb",
            retrieved_contexts=contexts,
        )

        mock_faith.assert_called_once_with(
            trace_id="trace-kb-001",
            question="How do I set up VPN?",
            answer="Follow chunk 1 and 2 instructions.",
            retrieved_contexts=contexts,
        )
        mock_rel.assert_called_once_with(
            trace_id="trace-kb-001",
            question="How do I set up VPN?",
            answer="Follow chunk 1 and 2 instructions.",
        )

    async def test_web_grounded_answer_runs_both_metrics(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock(return_value=0.89)
        mock_rel = MagicMock(return_value=0.94)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        contexts = ["Web snippet: Error code 0x800"]
        await run_online_evaluation_task(
            trace_id="trace-web-001",
            question="Fix 0x800",
            answer="Restart machine.",
            source_used="web",
            retrieved_contexts=contexts,
        )

        mock_faith.assert_called_once()
        mock_rel.assert_called_once()

    async def test_eval_metrics_config_filtering_faithfulness_only(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_metrics = ["faithfulness"]
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock(return_value=0.95)
        mock_rel = MagicMock()
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-faith-only",
            question="Q?",
            answer="A",
            source_used="kb",
            retrieved_contexts=["ctx"],
        )

        mock_faith.assert_called_once()
        mock_rel.assert_not_called()

    async def test_eval_metrics_config_filtering_relevancy_only(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_metrics = ["answer_relevancy"]
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock()
        mock_rel = MagicMock(return_value=0.91)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-rel-only",
            question="Q?",
            answer="A",
            source_used="kb",
            retrieved_contexts=["ctx"],
        )

        mock_faith.assert_not_called()
        mock_rel.assert_called_once()


@pytest.mark.asyncio
class TestTimeoutAndFailOpenSafety:
    """Verify evaluator gracefully catches timeouts and judge exceptions without raising."""

    async def test_eval_timeout_fails_open(self, base_eval_settings, monkeypatch):
        base_eval_settings.eval_timeout_seconds = 0.05
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        def slow_faithfulness(*args, **kwargs):
            time.sleep(0.2)
            return 1.0

        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", slow_faithfulness)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", MagicMock())

        # Must not raise asyncio.TimeoutError
        await run_online_evaluation_task(
            trace_id="trace-timeout-001",
            question="Slow question",
            answer="Slow answer",
            source_used="kb",
            retrieved_contexts=["ctx"],
        )

    async def test_eval_faithfulness_exception_fails_open(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock(side_effect=RuntimeError("Judge LLM API unavailable"))
        mock_rel = MagicMock(return_value=0.90)
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        # Must log warning and proceed to answer relevancy without raising
        await run_online_evaluation_task(
            trace_id="trace-fail-001",
            question="Fault question",
            answer="Fault answer",
            source_used="kb",
            retrieved_contexts=["ctx"],
        )

        mock_faith.assert_called_once()
        mock_rel.assert_called_once()

    async def test_eval_relevancy_exception_fails_open(self, base_eval_settings, monkeypatch):
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        mock_faith = MagicMock(return_value=0.88)
        mock_rel = MagicMock(side_effect=ValueError("DeepEval parser failure"))
        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", mock_faith)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", mock_rel)

        await run_online_evaluation_task(
            trace_id="trace-fail-002",
            question="Fault question 2",
            answer="Fault answer 2",
            source_used="kb",
            retrieved_contexts=["ctx"],
        )

        mock_faith.assert_called_once()
        mock_rel.assert_called_once()

    async def test_default_eval_timeout_is_120s(self):
        from app.core.config import Settings
        s = Settings()
        assert s.eval_timeout_seconds == 120.0

    async def test_eval_metrics_run_concurrently(self, base_eval_settings, monkeypatch):
        """Verify faithfulness and answer relevancy execute concurrently in threadpool."""
        monkeypatch.setattr("app.services.evaluation.get_settings", lambda: base_eval_settings)

        started = set()
        both_running = False

        def slow_f(*args, **kwargs):
            nonlocal both_running
            started.add("faithfulness")
            time.sleep(0.05)
            if "relevancy" in started:
                both_running = True
            return 0.9

        def slow_r(*args, **kwargs):
            nonlocal both_running
            started.add("relevancy")
            time.sleep(0.05)
            if "faithfulness" in started:
                both_running = True
            return 0.95

        monkeypatch.setattr("app.services.evaluation.score_trace_faithfulness", slow_f)
        monkeypatch.setattr("app.services.evaluation.score_trace_answer_relevancy", slow_r)

        t0 = time.perf_counter()
        await run_online_evaluation_task(
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
    """Verify POST /api/chat and POST /api/chat/stream dispatch run_online_evaluation_task."""

    def test_sync_chat_enqueues_background_evaluation(self, monkeypatch, register_mock_workflow):
        client = TestClient(app)

        mock_eval = AsyncMock()
        monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", mock_eval)

        register_mock_workflow(
            "fix_single_llm",
            answer="Test sync answer",
            source_used="kb",
            retrieved_contexts=["doc chunk 1", "doc chunk 2"],
            trace=["retrieve_kb", "generate_from_kb"],
            citations=[],
        )
        monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: "trace-sync-123")

        payload = {
            "question": "How to resolve sync issue?",
            "workflow": "fix_single_llm",
        }
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "Test sync answer"

        mock_eval.assert_called_once_with(
            trace_id="trace-sync-123",
            question="How to resolve sync issue?",
            answer="Test sync answer",
            source_used="kb",
            retrieved_contexts=["doc chunk 1", "doc chunk 2"],
        )

    @pytest.mark.asyncio
    async def test_streaming_chat_triggers_background_evaluation(self, monkeypatch, register_mock_workflow):
        eval_task_called = asyncio.Event()
        captured_kwargs = {}

        async def fake_eval_task(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            eval_task_called.set()

        monkeypatch.setattr("app.rag.workflows.runner.run_online_evaluation_task", fake_eval_task)

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
        monkeypatch.setattr("app.rag.workflows.runner.extract_trace_id", lambda handler: "trace-stream-456")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            async with ac.stream(
                "POST",
                "/api/chat/stream",
                json={"question": "Stream question?", "workflow": "fix_single_llm"},
            ) as resp:
                assert resp.status_code == 200
                _ = [line async for line in resp.aiter_lines()]

        # Wait briefly for background task
        await asyncio.wait_for(eval_task_called.wait(), timeout=2.0)
        assert captured_kwargs["trace_id"] == "trace-stream-456"
        assert captured_kwargs["question"] == "Stream question?"
        assert captured_kwargs["answer"] == "Streamed response token"
        assert captured_kwargs["source_used"] == "kb"
        assert captured_kwargs["retrieved_contexts"] == ["Stream doc chunk A"]
