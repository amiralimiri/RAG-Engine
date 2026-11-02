from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langfuse.model import TextPromptClient

from app.core.config import get_settings
from app.rag import classifiers, engine
from app.rag.classifiers import JevClassifierAdapter, _record_jev_generation
from app.rag.prompts import RenderedPrompt


class TestJEVCostAccounting:
    """Verifies deterministic unit token cost calculation in JEV generation observations."""

    def test_record_jev_generation_when_langfuse_disabled(self, monkeypatch):
        """When LANGFUSE_ENABLED=false, _record_jev_generation returns cleanly without errors."""
        monkeypatch.setattr(classifiers, "get_langfuse_client", lambda: None)
        # Should not throw any exception
        _record_jev_generation("test_disabled", "input", "output")

    def test_graph_nodes_succeed_when_langfuse_disabled(self, monkeypatch):
        """Workflow nodes execute cleanly when get_langfuse_client() is None."""
        monkeypatch.setattr(classifiers, "get_langfuse_client", lambda: None)
        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"route": MagicMock(choice="direct")}
        adapter = JevClassifierAdapter(classifier=mock_jev)

        res = adapter.route("hello")
        assert res.route == "direct"

    def test_record_jev_generation_computes_cost_details(self, monkeypatch):
        """_record_jev_generation accurately computes cost_details (input, output, total) in USD."""
        mock_client = MagicMock()
        mock_observation = MagicMock()
        mock_client.start_observation.return_value = mock_observation
        monkeypatch.setattr(classifiers, "get_langfuse_client", lambda: mock_client)

        # 40 chars = 10 input tokens; 20 chars = 5 output tokens
        input_text = "A" * 40
        output_text = "B" * 20

        # Override settings with known unit rates
        s = get_settings()
        monkeypatch.setattr(s, "jev_cost_per_1k_input_tokens", 0.0010)
        monkeypatch.setattr(s, "jev_cost_per_1k_output_tokens", 0.0020)

        _record_jev_generation("test_span", input_text, output_text)

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
        monkeypatch.setattr(classifiers, "get_langfuse_client", lambda: mock_client)

        s = get_settings()
        monkeypatch.setattr(s, "jev_cost_per_1k_input_tokens", 0.0008)
        monkeypatch.setattr(s, "jev_cost_per_1k_output_tokens", 0.0008)

        # 4 chars = 1 input token, 8 chars = 2 output tokens
        _record_jev_generation("test_default", "1234", "12345678")

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
        monkeypatch.setattr(classifiers, "get_langfuse_client", lambda: mock_client)

        mock_prompt = MagicMock(spec=TextPromptClient)
        mock_prompt.name = "rag:router:jev"
        mock_prompt.version = 3
        mock_prompt.is_fallback = False

        _record_jev_generation(
            "jev_router",
            "question",
            "kb",
            prompt_client=mock_prompt,
        )

        _, kwargs = mock_client.start_observation.call_args
        assert kwargs.get("prompt") is mock_prompt

    def test_record_jev_generation_handles_fallback_prompt_safely(self, monkeypatch):
        """When a fallback RenderedPrompt (prompt_fallback=True) is supplied, prompt is omitted from observation args."""
        mock_client = MagicMock()
        mock_client.start_observation.return_value = MagicMock()
        monkeypatch.setattr(classifiers, "get_langfuse_client", lambda: mock_client)

        rendered = RenderedPrompt(
            name="rag:router:jev",
            text="prompt text",
            config={"metadata": {"prompt_name": "rag:router:jev", "prompt_fallback": True}},
        )

        _record_jev_generation(
            "jev_router",
            "question",
            "kb",
            rendered=rendered,
        )

        _, kwargs = mock_client.start_observation.call_args
        # Should not pass the fallback prompt object as the Langfuse prompt argument
        assert "prompt" not in kwargs or kwargs["prompt"] is None
        # Safely recorded in metadata
        assert kwargs["metadata"].get("prompt_fallback") is True
        assert kwargs["metadata"].get("prompt_name") == "rag:router:jev"


class TestPromptCMSWiringAcrossJEVNodes:
    """Verifies PromptStore is wired across JEV adapter and generation nodes."""

    def test_route_question_jev_wires_prompt(self):
        """route fetches 'rag:router:jev' and passes it to _record_jev_generation."""
        mock_prompt = MagicMock(name="rag:router:jev", is_fallback=False)
        mock_store = MagicMock()
        mock_rendered = RenderedPrompt(
            name="rag:router:jev",
            text="template",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
            raw_prompt=mock_prompt,
        )
        mock_store.render.return_value = mock_rendered

        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"route": MagicMock(choice="kb")}
        adapter = JevClassifierAdapter(classifier=mock_jev, prompt_store=mock_store)

        with patch("app.rag.classifiers._record_jev_generation") as mock_record:
            res = adapter.route("How to connect to VPN?")
            assert res.route == "kb"

            mock_store.render.assert_called_with("rag:router:jev", config=None, question="How to connect to VPN?")
            mock_record.assert_called_once()
            _, kwargs = mock_record.call_args
            assert kwargs.get("rendered") is mock_rendered

    def test_grade_kb_jev_wires_prompt(self):
        """grade_kb fetches 'rag:grade_kb:jev' and passes it to _record_jev_generation."""
        mock_prompt = MagicMock(name="rag:grade_kb:jev", is_fallback=False)
        mock_store = MagicMock()
        mock_rendered = RenderedPrompt(
            name="rag:grade_kb:jev",
            text="template",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
            raw_prompt=mock_prompt,
        )
        mock_store.render.return_value = mock_rendered

        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"grade": MagicMock(choice="good")}
        adapter = JevClassifierAdapter(classifier=mock_jev, prompt_store=mock_store)

        doc = MagicMock()
        doc.metadata = {"source": "vpn.md"}
        doc.page_content = "VPN guide content"

        with patch("app.rag.classifiers._record_jev_generation") as mock_record:
            res = adapter.grade_kb("How to connect to VPN?", "VPN guide", [doc])
            assert res.grade == "good"
            mock_store.render.assert_called_once()
            assert mock_record.call_args[1].get("rendered") is mock_rendered

    def test_grade_web_jev_wires_prompt(self):
        """grade_web fetches 'rag:grade_web:jev' and passes it to _record_jev_generation."""
        mock_prompt = MagicMock(name="rag:grade_web:jev", is_fallback=False)
        mock_store = MagicMock()
        mock_rendered = RenderedPrompt(
            name="rag:grade_web:jev",
            text="template",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
            raw_prompt=mock_prompt,
        )
        mock_store.render.return_value = mock_rendered

        mock_jev = MagicMock()
        mock_jev.invoke.return_value.choices = {"grade": MagicMock(choice="good")}
        adapter = JevClassifierAdapter(classifier=mock_jev, prompt_store=mock_store)

        with patch("app.rag.classifiers._record_jev_generation") as mock_record:
            res = adapter.grade_web("How to connect to VPN?", "VPN guide", "Web content about VPN")
            assert res.grade == "good"
            mock_store.render.assert_called_once()
            assert mock_record.call_args[1].get("rendered") is mock_rendered

    @pytest.mark.asyncio
    async def test_rewrite_query_wires_prompt(self):
        """rewrite_query compiles 'rag:rewrite_query'."""
        mock_rewrite_prompt = MagicMock(is_fallback=False)
        mock_gen_prompt = MagicMock(is_fallback=False)
        mock_store = MagicMock()
        mock_rewrite_rendered = RenderedPrompt(
            name="rag:rewrite_query",
            text="Compiled rewrite prompt",
            config={"metadata": {"langfuse_prompt": mock_rewrite_prompt}},
            raw_prompt=mock_rewrite_prompt,
        )
        mock_gen_rendered = RenderedPrompt(
            name="rag:generate_kb",
            text="Compiled gen prompt",
            config={"metadata": {"langfuse_prompt": mock_gen_prompt}},
            raw_prompt=mock_gen_prompt,
        )

        def fake_render(name, **kwargs):
            if name == "rag:rewrite_query":
                return mock_rewrite_rendered
            return mock_gen_rendered

        mock_store.render.side_effect = fake_render

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="improved search query"))

        mock_classifier = MagicMock()
        mock_classifier.route.return_value = MagicMock(route="kb")
        mock_classifier.grade_kb.side_effect = [MagicMock(grade="weak"), MagicMock(grade="good")]
        mock_classifier.grade_web.return_value = MagicMock(grade="weak")

        kb_tool = MagicMock()
        kb_tool.ainvoke = AsyncMock(return_value=("kb content", {"documents": [], "citations": []}))
        web_tool = MagicMock()
        web_tool.ainvoke = AsyncMock(return_value=("web content", {"retrieved_contexts": [], "citations": []}))

        graph = engine.create_agentic_rag_graph(
            mock_classifier,
            model=mock_llm,
            kb_tool=kb_tool,
            web_tool=web_tool,
            prompt_store=mock_store,
        )
        state = {
            "question": "VPN broken",
            "current_query": "vpn help",
            "retry_count": 0,
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "trace": [],
            "citations": [],
            "retrieved_contexts": [],
        }
        await graph.ainvoke(state)

        rewrite_calls = [
            c
            for c in mock_llm.ainvoke.call_args_list
            if c[1].get("config", {}).get("metadata", {}).get("langfuse_prompt") == mock_rewrite_prompt
        ]
        assert len(rewrite_calls) >= 1
        assert rewrite_calls[0][0][0] == "Compiled rewrite prompt"

    @pytest.mark.asyncio
    async def test_generate_from_kb_wires_prompt(self):
        """generate_from_kb compiles 'rag:generate_kb'."""
        mock_prompt = MagicMock(is_fallback=False)
        mock_store = MagicMock()
        mock_rendered = RenderedPrompt(
            name="rag:generate_kb",
            text="Compiled KB prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
            raw_prompt=mock_prompt,
        )
        mock_store.render.return_value = mock_rendered

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="KB Answer"))

        mock_classifier = MagicMock()
        mock_classifier.route.return_value = MagicMock(route="kb")
        mock_classifier.grade_kb.return_value = MagicMock(grade="good")

        doc = MagicMock()
        doc.metadata = {"source": "vpn.md"}
        doc.page_content = "VPN Guide details"

        kb_tool = MagicMock()
        kb_tool.ainvoke = AsyncMock(return_value=("kb content", {"documents": [doc], "citations": []}))

        graph = engine.create_agentic_rag_graph(
            mock_classifier,
            model=mock_llm,
            kb_tool=kb_tool,
            prompt_store=mock_store,
        )
        state = {
            "question": "Setup VPN",
            "current_query": "Setup VPN",
            "retry_count": 0,
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "trace": [],
            "citations": [],
            "retrieved_contexts": [],
        }
        res = await graph.ainvoke(state)

        assert res["answer"] == "KB Answer"
        mock_store.render.assert_called_once()
        args, kwargs = mock_llm.ainvoke.call_args
        assert args[0] == "Compiled KB prompt"
        assert kwargs["config"]["metadata"]["langfuse_prompt"] == mock_prompt

    @pytest.mark.asyncio
    async def test_generate_from_web_wires_prompt(self):
        """generate_from_web compiles 'rag:generate_web'."""
        mock_prompt = MagicMock(is_fallback=False)
        mock_store = MagicMock()
        mock_rendered = RenderedPrompt(
            name="rag:generate_web",
            text="Compiled Web prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
            raw_prompt=mock_prompt,
        )
        mock_store.render.return_value = mock_rendered

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="Web Answer"))

        mock_classifier = MagicMock()
        mock_classifier.route.return_value = MagicMock(route="kb")
        mock_classifier.grade_kb.return_value = MagicMock(grade="weak")
        mock_classifier.grade_web.return_value = MagicMock(grade="good")

        kb_tool = MagicMock()
        kb_tool.ainvoke = AsyncMock(return_value=("kb content", {"documents": [], "citations": []}))
        web_tool = MagicMock()
        web_tool.ainvoke = AsyncMock(return_value=("External web info", {"retrieved_contexts": [], "citations": []}))

        graph = engine.create_agentic_rag_graph(
            mock_classifier,
            model=mock_llm,
            kb_tool=kb_tool,
            web_tool=web_tool,
            prompt_store=mock_store,
        )
        state = {
            "question": "Setup VPN",
            "current_query": "Setup VPN",
            "retry_count": 0,
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "trace": [],
            "citations": [],
            "retrieved_contexts": [],
        }
        res = await graph.ainvoke(state)

        assert res["answer"] == "Web Answer"
        mock_store.render.assert_called_once()
        args, kwargs = mock_llm.ainvoke.call_args
        assert args[0] == "Compiled Web prompt"
        assert kwargs["config"]["metadata"]["langfuse_prompt"] == mock_prompt

    @pytest.mark.asyncio
    async def test_direct_answer_wires_prompt(self):
        """direct_answer compiles 'rag:direct_answer'."""
        mock_prompt = MagicMock(is_fallback=False)
        mock_store = MagicMock()
        mock_rendered = RenderedPrompt(
            name="rag:direct_answer",
            text="Compiled Direct prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
            raw_prompt=mock_prompt,
        )
        mock_store.render.return_value = mock_rendered

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="Direct Answer"))

        state = {
            "question": "Hi",
        }
        res = await engine.direct_answer(state, model=mock_llm, prompt_store=mock_store)

        assert res["answer"] == "Direct Answer"
        mock_store.render.assert_called_once()
        mock_llm.ainvoke.assert_called_once_with(
            "Compiled Direct prompt",
            config={"metadata": {"langfuse_prompt": mock_prompt}},
        )


class TestChatOpenAIStreamUsage:
    """Verifies stream_usage=True is enabled on ChatOpenAI instances."""

    def test_engine_has_stream_usage_enabled(self, monkeypatch):
        client = engine.get_chat_model()
        assert getattr(client, "stream_usage", None) is True
