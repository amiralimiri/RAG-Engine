import ast
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langfuse.model import TextPromptClient

from app.core.config import get_settings
from app.rag import fix_single_jev
from app.services.prompts import FallbackPrompt


class TestJEVCostAccounting:
    """Verifies deterministic unit token cost calculation in JEV generation observations."""

    def test_record_jev_generation_when_langfuse_disabled(self, monkeypatch):
        """When LANGFUSE_ENABLED=false, _record_jev_generation returns cleanly without errors."""
        monkeypatch.setattr(fix_single_jev, "get_langfuse_client", lambda: None)
        # Should not throw any exception
        fix_single_jev._record_jev_generation("test_disabled", "input", "output")

    def test_graph_nodes_succeed_when_langfuse_disabled(self, monkeypatch):
        """Workflow nodes execute cleanly when get_langfuse_client() is None."""
        monkeypatch.setattr(fix_single_jev, "get_langfuse_client", lambda: None)
        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"route": MagicMock(choice="direct")}
        monkeypatch.setattr(fix_single_jev, "jev", lambda: mock_jev)

        state = {
            "question": "hello",
            "current_query": "",
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "retry_count": 0,
            "trace": [],
            "citations": [],
        }
        res = fix_single_jev.route_question_jev(state)
        assert res["source_used"] == "direct"

    def test_record_jev_generation_computes_cost_details(self, monkeypatch):
        """_record_jev_generation accurately computes cost_details (input, output, total) in USD."""
        mock_client = MagicMock()
        mock_observation = MagicMock()
        mock_client.start_observation.return_value = mock_observation
        monkeypatch.setattr(fix_single_jev, "get_langfuse_client", lambda: mock_client)

        # 40 chars = 10 input tokens; 20 chars = 5 output tokens
        input_text = "A" * 40
        output_text = "B" * 20

        # Override settings with known unit rates
        s = get_settings()
        monkeypatch.setattr(s, "jev_cost_per_1k_input_tokens", 0.0010)
        monkeypatch.setattr(s, "jev_cost_per_1k_output_tokens", 0.0020)

        fix_single_jev._record_jev_generation("test_span", input_text, output_text)

        mock_client.start_observation.assert_called_once()
        _, kwargs = mock_client.start_observation.call_args

        assert kwargs["name"] == "test_span"
        assert kwargs["as_type"] == "generation"

        # Verify usage details: 10 input, 5 output, 15 total
        assert kwargs["usage_details"] == {
            "input": 10,
            "output": 5,
            "total": 15,
        }

        # Expected cost calculation:
        # input_cost = (10 * 0.0010) / 1000.0 = 0.000010
        # output_cost = (5 * 0.0020) / 1000.0 = 0.000010
        # total_cost = 0.000020
        expected_cost = {
            "input": pytest.approx(0.000010, rel=1e-6),
            "output": pytest.approx(0.000010, rel=1e-6),
            "total": pytest.approx(0.000020, rel=1e-6),
        }
        assert "cost_details" in kwargs
        assert kwargs["cost_details"]["input"] == expected_cost["input"]
        assert kwargs["cost_details"]["output"] == expected_cost["output"]
        assert kwargs["cost_details"]["total"] == expected_cost["total"]

        # Verify observation.update and observation.end are called to persist costs
        mock_observation.update.assert_called_once_with(
            usage_details=kwargs["usage_details"],
            cost_details=kwargs["cost_details"],
            output=output_text,
        )
        mock_observation.end.assert_called_once()

    def test_record_jev_generation_default_settings_cost(self, monkeypatch):
        """Verify cost calculation with default 0.0008 USD per 1k tokens."""
        mock_client = MagicMock()
        mock_client.start_observation.return_value = MagicMock()
        monkeypatch.setattr(fix_single_jev, "get_langfuse_client", lambda: mock_client)

        s = get_settings()
        monkeypatch.setattr(s, "jev_cost_per_1k_input_tokens", 0.0008)
        monkeypatch.setattr(s, "jev_cost_per_1k_output_tokens", 0.0008)

        # 4 chars = 1 input token, 8 chars = 2 output tokens
        fix_single_jev._record_jev_generation("test_default", "1234", "12345678")

        _, kwargs = mock_client.start_observation.call_args
        assert kwargs["usage_details"] == {"input": 1, "output": 2, "total": 3}
        assert kwargs["cost_details"]["input"] == pytest.approx(1 * 0.0008 / 1000.0)
        assert kwargs["cost_details"]["output"] == pytest.approx(2 * 0.0008 / 1000.0)
        assert kwargs["cost_details"]["total"] == pytest.approx(3 * 0.0008 / 1000.0)


class TestPromptObservationLinking:
    """Verifies Prompt CMS linking on JEV observations."""

    def test_record_jev_generation_links_active_prompt_client(self, monkeypatch):
        """When an active PromptClient (not fallback) is supplied, it is passed to start_observation."""
        mock_client = MagicMock()
        mock_client.start_observation.return_value = MagicMock()
        monkeypatch.setattr(fix_single_jev, "get_langfuse_client", lambda: mock_client)

        mock_prompt = MagicMock(spec=TextPromptClient)
        mock_prompt.name = "rag:router:jev"
        mock_prompt.version = 3
        mock_prompt.is_fallback = False

        fix_single_jev._record_jev_generation(
            "jev_router",
            "question",
            "kb",
            prompt_client=mock_prompt,
        )

        _, kwargs = mock_client.start_observation.call_args
        assert kwargs.get("prompt") is mock_prompt

    def test_record_jev_generation_handles_fallback_prompt_safely(self, monkeypatch):
        """When a FallbackPrompt (is_fallback=True) is supplied, prompt is omitted from observation args."""
        mock_client = MagicMock()
        mock_client.start_observation.return_value = MagicMock()
        monkeypatch.setattr(fix_single_jev, "get_langfuse_client", lambda: mock_client)

        fallback_prompt = FallbackPrompt(name="rag:router:jev", template="prompt text", version=0)
        assert fallback_prompt.is_fallback is True

        fix_single_jev._record_jev_generation(
            "jev_router",
            "question",
            "kb",
            prompt_client=fallback_prompt,
        )

        _, kwargs = mock_client.start_observation.call_args
        # Should not pass the fallback prompt object as the Langfuse prompt argument
        assert "prompt" not in kwargs or kwargs["prompt"] is None
        # Safely recorded in metadata
        assert kwargs["metadata"].get("prompt_fallback") is True
        assert kwargs["metadata"].get("prompt_name") == "rag:router:jev"


class TestPromptCMSWiringAcrossJEVNodes:
    """Verifies get_workflow_prompt is wired across all nodes in fix_single_jev."""

    def test_route_question_jev_wires_prompt(self, monkeypatch):
        """route_question_jev fetches 'rag:router:jev' and passes it to _record_jev_generation."""
        mock_prompt = FallbackPrompt("rag:router:jev", "template", version=1, is_fallback=False)
        mock_get_prompt = MagicMock(return_value=mock_prompt)
        monkeypatch.setattr(fix_single_jev, "get_workflow_prompt", mock_get_prompt)

        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"route": MagicMock(choice="kb")}
        monkeypatch.setattr(fix_single_jev, "jev", lambda: mock_jev)

        with patch.object(fix_single_jev, "_record_jev_generation") as mock_record:
            state = {"question": "How to connect to VPN?", "current_query": ""}
            res = fix_single_jev.route_question_jev(state)
            assert res["source_used"] == "kb"

            mock_get_prompt.assert_called_with("rag:router:jev")
            mock_record.assert_called_once()
            _, kwargs = mock_record.call_args
            assert kwargs.get("prompt_client") is mock_prompt

    def test_grade_kb_jev_wires_prompt(self, monkeypatch):
        """grade_kb_jev and grade_kb_jev_2 fetch 'rag:grade_kb:jev' and pass it to _record_jev_generation."""
        mock_prompt = FallbackPrompt("rag:grade_kb:jev", "template", version=1, is_fallback=False)
        mock_get_prompt = MagicMock(return_value=mock_prompt)
        monkeypatch.setattr(fix_single_jev, "get_workflow_prompt", mock_get_prompt)

        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"grade": MagicMock(choice="good")}
        monkeypatch.setattr(fix_single_jev, "jev", lambda: mock_jev)

        doc = MagicMock()
        doc.metadata = {"source": "vpn.md"}
        doc.page_content = "VPN guide content"

        with patch.object(fix_single_jev, "_record_jev_generation") as mock_record:
            state = {
                "question": "How to connect to VPN?",
                "current_query": "VPN guide",
                "kb_docs": [doc],
            }
            # Test grade_kb_jev
            res = fix_single_jev.grade_kb_jev(state)
            assert res["kb_grade"] == "good"
            mock_get_prompt.assert_called_with("rag:grade_kb:jev")
            assert mock_record.call_args[1].get("prompt_client") is mock_prompt

            # Test grade_kb_jev_2
            mock_record.reset_mock()
            mock_get_prompt.reset_mock()
            res2 = fix_single_jev.grade_kb_jev_2(state)
            assert res2["kb_grade"] == "good"
            mock_get_prompt.assert_called_with("rag:grade_kb:jev")
            assert mock_record.call_args[1].get("prompt_client") is mock_prompt

    def test_grade_web_jev_wires_prompt(self, monkeypatch):
        """grade_web_jev and grade_web_jev_2 fetch 'rag:grade_web:jev' and pass it to _record_jev_generation."""
        mock_prompt = FallbackPrompt("rag:grade_web:jev", "template", version=1, is_fallback=False)
        mock_get_prompt = MagicMock(return_value=mock_prompt)
        monkeypatch.setattr(fix_single_jev, "get_workflow_prompt", mock_get_prompt)

        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"grade": MagicMock(choice="good")}
        monkeypatch.setattr(fix_single_jev, "jev", lambda: mock_jev)

        with patch.object(fix_single_jev, "_record_jev_generation") as mock_record:
            state = {
                "question": "How to connect to VPN?",
                "current_query": "VPN guide",
                "web_results": "Web content about VPN",
            }
            # Test grade_web_jev
            res = fix_single_jev.grade_web_jev(state)
            assert res["web_grade"] == "good"
            mock_get_prompt.assert_called_with("rag:grade_web:jev")
            assert mock_record.call_args[1].get("prompt_client") is mock_prompt

            # Test grade_web_jev_2
            mock_record.reset_mock()
            mock_get_prompt.reset_mock()
            res2 = fix_single_jev.grade_web_jev_2(state)
            assert res2["web_grade"] == "good"
            mock_get_prompt.assert_called_with("rag:grade_web:jev")
            assert mock_record.call_args[1].get("prompt_client") is mock_prompt

    def test_rewrite_query_wires_prompt(self, monkeypatch):
        """rewrite_query_2 in fix_single_jev compiles 'rag:rewrite_query'."""
        mock_prompt = MagicMock()
        mock_prompt.compile.return_value = "Compiled rewrite prompt"
        mock_get_prompt = MagicMock(return_value=mock_prompt)
        monkeypatch.setattr(fix_single_jev, "get_workflow_prompt", mock_get_prompt)

        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "improved search query"
        monkeypatch.setattr(fix_single_jev, "llm", lambda: mock_llm)

        state = {
            "question": "VPN broken",
            "current_query": "vpn help",
            "retry_count": 0,
        }
        res = fix_single_jev.rewrite_query_2(state)

        assert res["current_query"] == "improved search query"
        mock_get_prompt.assert_called_with("rag:rewrite_query")
        mock_prompt.compile.assert_called_once_with(
            question="VPN broken",
            current_query="vpn help",
        )
        mock_llm.invoke.assert_called_once_with(
            "Compiled rewrite prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
        )

    @pytest.mark.asyncio
    async def test_generate_from_kb_wires_prompt(self, monkeypatch):
        """generate_from_kb compiles 'rag:generate_kb'."""
        mock_prompt = MagicMock()
        mock_prompt.compile.return_value = "Compiled KB prompt"
        mock_get_prompt = MagicMock(return_value=mock_prompt)
        monkeypatch.setattr(fix_single_jev, "get_workflow_prompt", mock_get_prompt)

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="KB Answer"))
        monkeypatch.setattr(fix_single_jev, "llm", lambda: mock_llm)

        doc = MagicMock()
        doc.metadata = {"source": "vpn_docs.txt"}
        doc.page_content = "To setup VPN..."

        state = {
            "question": "Setup VPN",
            "kb_docs": [doc],
        }
        res = await fix_single_jev.generate_from_kb(state)

        assert res["answer"] == "KB Answer"
        mock_get_prompt.assert_called_with("rag:generate_kb")
        mock_prompt.compile.assert_called_once()
        mock_llm.ainvoke.assert_called_once_with(
            "Compiled KB prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
        )

    @pytest.mark.asyncio
    async def test_generate_from_web_wires_prompt(self, monkeypatch):
        """generate_from_web compiles 'rag:generate_web'."""
        mock_prompt = MagicMock()
        mock_prompt.compile.return_value = "Compiled Web prompt"
        mock_get_prompt = MagicMock(return_value=mock_prompt)
        monkeypatch.setattr(fix_single_jev, "get_workflow_prompt", mock_get_prompt)

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="Web Answer"))
        monkeypatch.setattr(fix_single_jev, "llm", lambda: mock_llm)

        state = {
            "question": "Setup VPN",
            "web_results": "External web info",
        }
        res = await fix_single_jev.generate_from_web(state)

        assert res["answer"] == "Web Answer"
        mock_get_prompt.assert_called_with("rag:generate_web")
        mock_prompt.compile.assert_called_once_with(
            question="Setup VPN",
            web_results="External web info",
        )
        mock_llm.ainvoke.assert_called_once_with(
            "Compiled Web prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
        )

    @pytest.mark.asyncio
    async def test_direct_answer_wires_prompt(self, monkeypatch):
        """direct_answer compiles 'rag:direct_answer'."""
        mock_prompt = MagicMock()
        mock_prompt.compile.return_value = "Compiled Direct prompt"
        mock_get_prompt = MagicMock(return_value=mock_prompt)
        monkeypatch.setattr(fix_single_jev, "get_workflow_prompt", mock_get_prompt)

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="Hello there!"))
        monkeypatch.setattr(fix_single_jev, "llm", lambda: mock_llm)

        state = {
            "question": "Hello",
        }
        res = await fix_single_jev.direct_answer(state)

        assert res["answer"] == "Hello there!"
        mock_get_prompt.assert_called_with("rag:direct_answer")
        mock_prompt.compile.assert_called_once_with(question="Hello")
        mock_llm.ainvoke.assert_called_once_with(
            "Compiled Direct prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
        )


class TestModuleDecoupling:
    """Verifies 100% zero-coupling between fix_single_llm and fix_single_jev."""

    def test_fix_single_modules_do_not_import_each_other(self):
        """Ensure fix_single_llm and fix_single_jev have zero cross-imports in source code."""
        with open("app/rag/fix_single_jev.py", "r", encoding="utf-8") as f:
            jev_tree = ast.parse(f.read())

        with open("app/rag/fix_single_llm.py", "r", encoding="utf-8") as f:
            llm_tree = ast.parse(f.read())

        # Check imports in fix_single_jev
        for node in ast.walk(jev_tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "fix_single_llm" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                assert node.module is not None
                assert "fix_single_llm" not in node.module

        # Check imports in fix_single_llm
        for node in ast.walk(llm_tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "fix_single_jev" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                assert node.module is not None
                assert "fix_single_jev" not in node.module


class TestChatOpenAIStreamUsage:
    """Verifies stream_usage=True is enabled on ChatOpenAI instances across workflows."""

    def test_fix_single_llm_has_stream_usage_enabled(self, monkeypatch):
        from app.rag import fix_single_llm
        fix_single_llm._llm = None
        client = fix_single_llm.llm()
        assert getattr(client, "stream_usage", None) is True

    def test_fix_single_jev_has_stream_usage_enabled(self, monkeypatch):
        from app.rag import fix_single_jev
        fix_single_jev._llm = None
        client = fix_single_jev.llm()
        assert getattr(client, "stream_usage", None) is True


class TestLLMCostAccounting:
    """Verifies deterministic unit token cost calculation and Langfuse generation update for LLM calls."""

    def test_extract_llm_token_counts_from_llm_output(self):
        from app.core.telemetry import extract_llm_token_counts
        from langchain_core.outputs import LLMResult

        res = LLMResult(
            generations=[],
            llm_output={"token_usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}},
        )
        usage = extract_llm_token_counts(res)
        assert usage == {"input": 120, "output": 40, "total": 160}

    def test_extract_llm_token_counts_from_message_usage_metadata(self):
        from app.core.telemetry import extract_llm_token_counts
        from langchain_core.outputs import LLMResult, ChatGeneration
        from langchain_core.messages import AIMessage

        res = LLMResult(
            generations=[[
                ChatGeneration(message=AIMessage(
                    content="test answer",
                    usage_metadata={"input_tokens": 80, "output_tokens": 25, "total_tokens": 105},
                ))
            ]],
        )
        usage = extract_llm_token_counts(res)
        assert usage == {"input": 80, "output": 25, "total": 105}

    def test_calculate_llm_cost_details(self, monkeypatch):
        from app.core.telemetry import calculate_llm_cost_details

        s = get_settings()
        monkeypatch.setattr(s, "llm_cost_per_1k_input_tokens", 0.0015)
        monkeypatch.setattr(s, "llm_cost_per_1k_output_tokens", 0.0020)

        # 1000 input tokens = $0.0015, 2000 output tokens = $0.0040, total = $0.0055
        cost = calculate_llm_cost_details(1000, 2000)
        assert cost["input"] == pytest.approx(0.0015)
        assert cost["output"] == pytest.approx(0.0040)
        assert cost["total"] == pytest.approx(0.0055)

    def test_callback_handler_on_llm_end_updates_generation_cost(self, monkeypatch):
        """Verifies LangfuseCallbackHandler updates generation observation with cost_details on LLM completion."""
        import uuid
        from langfuse.langchain import CallbackHandler
        from app.core.telemetry import LangfuseCallbackHandler
        from langchain_core.outputs import LLMResult, ChatGeneration
        from langchain_core.messages import AIMessage

        mock_obs = MagicMock()
        handler = LangfuseCallbackHandler(public_key="test-key")
        run_id = uuid.uuid4()
        handler._runs[run_id] = mock_obs

        res = LLMResult(
            generations=[[
                ChatGeneration(message=AIMessage(
                    content="Hello world",
                    usage_metadata={"input_tokens": 200, "output_tokens": 50, "total_tokens": 250},
                ))
            ]],
        )

        with patch.object(CallbackHandler, "on_llm_end"):
            handler.on_llm_end(res, run_id=run_id)

        assert mock_obs.update.called
        update_call = mock_obs.update.call_args[1]
        assert "cost_details" in update_call
        assert update_call["usage_details"] == {"input": 200, "output": 50, "total": 250}

        s = get_settings()
        expected_input_cost = (200 * s.llm_cost_per_1k_input_tokens) / 1000.0
        expected_output_cost = (50 * s.llm_cost_per_1k_output_tokens) / 1000.0
        assert update_call["cost_details"]["input"] == pytest.approx(expected_input_cost)
        assert update_call["cost_details"]["output"] == pytest.approx(expected_output_cost)
        assert update_call["cost_details"]["total"] == pytest.approx(expected_input_cost + expected_output_cost)

