"""Conformance unit tests for legacy compatibility facade and test-shim isolation."""

from __future__ import annotations

import pytest
from typing import Any

from app.api.schemas import ChatRequest, ChatResponse
from app.rag import fix_single_llm, fix_single_jev
from app.rag.workflows.base import EvalContext, WorkflowDefinition
from app.rag.workflows.legacy import (
    WORKFLOW_ASKS,
    WORKFLOW_GRAPHS,
    _LegacyUnifiedWorkflowAdapter,
    _sync_legacy_registry,
    reset_legacy_defaults,
)
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.runner import get_workflow_runner
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow


@pytest.fixture(autouse=True)
def clean_legacy_environment():
    """Ensure clean legacy defaults before and after each test."""
    reset_legacy_defaults()
    yield
    reset_legacy_defaults()


class TestLegacyUnifiedWorkflowAdapter:
    """Tests verifying _LegacyUnifiedWorkflowAdapter protocol conformance and execution."""

    def test_protocol_conformance(self):
        """_LegacyUnifiedWorkflowAdapter must satisfy WorkflowDefinition runtime protocol."""
        adapter = _LegacyUnifiedWorkflowAdapter("fix_single_llm")
        assert isinstance(adapter, WorkflowDefinition)
        assert adapter.info.id == "fix_single_llm"
        assert adapter.info.name == "Legacy Adapted fix_single_llm"
        assert "retrieve_kb" in adapter.info.step_labels
        assert "generate_from_kb" in adapter.info.generation_nodes

    def test_state_transformation_and_response_mapping(self):
        """Adapter correctly normalizes request to state and state to ChatResponse."""
        adapter = _LegacyUnifiedWorkflowAdapter("fix_single_llm")
        req = ChatRequest(
            question="What is the refund policy?",
            session_id="sess-100",
            user_id="user-200",
            trace_id="tid-300",
        )
        initial_state = adapter.to_initial_state(req, trace_id="tid-override")
        assert initial_state["question"] == "What is the refund policy?"
        assert initial_state["session_id"] == "sess-100"
        assert initial_state["trace_id"] == "tid-override"

        final_state = {
            "answer": "Refunds are processed in 30 days.",
            "source_used": "knowledge_base",
            "trace": ["node1", "node2"],
            "citations": ["doc1.pdf"],
            "current_query": "refund policy",
            "trace_id": "tid-override",
        }
        resp = adapter.to_chat_response(final_state)
        assert isinstance(resp, ChatResponse)
        assert resp.workflow == "fix_single_llm"
        assert resp.answer == "Refunds are processed in 30 days."
        assert resp.source_used == "knowledge_base"
        assert resp.trace == ["node1", "node2"]
        assert resp.citations == ["doc1.pdf"]
        assert resp.rewritten_query == "refund policy"
        assert resp.trace_id == "tid-override"

    def test_extract_eval_context(self):
        """Adapter extracts EvalContext properly from state."""
        adapter = _LegacyUnifiedWorkflowAdapter("fix_single_llm")
        final_state = {
            "source_used": "web_search",
            "retrieved_contexts": ["context passage 1", "context passage 2"],
        }
        eval_ctx = adapter.extract_eval_context(final_state)
        assert isinstance(eval_ctx, EvalContext)
        assert eval_ctx.source_used == "web_search"
        assert eval_ctx.retrieved_contexts == ["context passage 1", "context passage 2"]

    def test_handle_error_synthesis(self):
        """Adapter synthesizes fallback state when an error is caught."""
        adapter = _LegacyUnifiedWorkflowAdapter("fix_single_llm")
        exc = RuntimeError("Database unreachable")
        err_state = adapter.handle_error(exc, {"question": "Test query"})
        assert "Inference error: Database unreachable" in err_state["answer"]
        assert err_state["source_used"] == "direct"
        assert err_state["question"] == "Test query"

    def test_invoke_ask_fn_with_kwargs(self):
        """Adapter invokes ask_fn accepting **kwargs cleanly."""
        calls: list[dict[str, Any]] = []

        def mock_ask(question: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"question": question, "kwargs": kwargs})
            return {"answer": f"Answer for {question}", "source_used": "direct"}

        adapter = _LegacyUnifiedWorkflowAdapter("fix_single_llm", ask_fn=mock_ask)
        graph = adapter.get_compiled_graph()
        state = {"question": "How to reset password?", "session_id": "s1", "user_id": "u1"}
        config = {"configurable": {"thread_id": "t1"}, "metadata": {"meta": "val"}}

        result = graph.invoke(state, config=config)
        assert result["answer"] == "Answer for How to reset password?"
        assert len(calls) == 1
        assert calls[0]["question"] == "How to reset password?"
        assert calls[0]["kwargs"]["session_id"] == "t1"
        assert calls[0]["kwargs"]["user_id"] == "u1"
        assert calls[0]["kwargs"]["metadata"] == {"meta": "val"}

    def test_invoke_ask_fn_positional_only_or_simple(self):
        """Adapter invokes simple callables without **kwargs without raising TypeError."""
        def simple_ask(question: str) -> dict[str, Any]:
            return {"answer": f"Simple: {question}"}

        adapter = _LegacyUnifiedWorkflowAdapter("fix_single_llm", ask_fn=simple_ask)
        graph = adapter.get_compiled_graph()
        state = {"question": "Hello?", "session_id": "s1"}
        result = graph.invoke(state)
        assert result["answer"] == "Simple: Hello?"

    def test_invoke_runnable_graph(self):
        """Adapter invokes runnable graph when ask_fn is not provided."""
        class MockRunnable:
            def invoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
                return {"answer": f"Graph answer: {state.get('question')}"}

        adapter = _LegacyUnifiedWorkflowAdapter("fix_single_llm", graph=MockRunnable())
        graph = adapter.get_compiled_graph()
        result = graph.invoke({"question": "Test graph"})
        assert result["answer"] == "Graph answer: Test graph"

    def test_astream_events_delegation_and_error(self):
        """astream_events delegates to graph if available, else raises NotImplementedError."""
        # No graph -> NotImplementedError
        adapter_no_graph = _LegacyUnifiedWorkflowAdapter("fix_single_llm", ask_fn=lambda q: {"answer": q})
        compiled_no_graph = adapter_no_graph.get_compiled_graph()
        with pytest.raises(NotImplementedError, match="Legacy ask workflow does not implement streaming"):
            compiled_no_graph.astream_events({"question": "hi"})

        # With graph -> delegates
        class MockStreamingGraph:
            def astream_events(self, *args: Any, **kwargs: Any) -> list[str]:
                return ["event1", "event2"]

        adapter_with_graph = _LegacyUnifiedWorkflowAdapter("fix_single_llm", graph=MockStreamingGraph())
        compiled_with_graph = adapter_with_graph.get_compiled_graph()
        assert compiled_with_graph.astream_events({"question": "hi"}) == ["event1", "event2"]


class TestLegacyDictionarySynchronization:
    """Tests verifying dictionary mutations update WorkflowRegistry and deletion resets defaults."""

    def test_workflow_asks_mutation_registers_adapter(self):
        """Mutating WORKFLOW_ASKS replaces canonical workflow with _LegacyUnifiedWorkflowAdapter."""
        registry = get_workflow_registry()
        # Verify initial default state
        assert isinstance(registry.get("fix_single_llm"), SelfCorrectiveRagWorkflow)

        def mock_llm_ask(question: str, **kwargs: Any) -> dict[str, Any]:
            return {"answer": "Patched LLM response", "source_used": "knowledge_base"}

        WORKFLOW_ASKS["fix_single_llm"] = mock_llm_ask
        wf = registry.get("fix_single_llm")
        assert isinstance(wf, _LegacyUnifiedWorkflowAdapter)
        assert wf.ask_fn is mock_llm_ask

        # Verify execution through WorkflowRunner
        runner = get_workflow_runner()
        resp = runner.execute(ChatRequest(workflow="fix_single_llm", question="Query"))
        assert resp.answer == "Patched LLM response"
        assert resp.source_used == "knowledge_base"

    def test_workflow_graphs_mutation_registers_adapter(self):
        """Mutating WORKFLOW_GRAPHS replaces canonical workflow with _LegacyUnifiedWorkflowAdapter."""
        registry = get_workflow_registry()

        class MockCustomGraph:
            def invoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
                return {"answer": "Graph patched"}

        WORKFLOW_GRAPHS["fix_single_jev"] = MockCustomGraph()
        wf = registry.get("fix_single_jev")
        assert isinstance(wf, _LegacyUnifiedWorkflowAdapter)
        assert isinstance(wf.graph, MockCustomGraph)

    def test_deleting_canonical_entry_resets_defaults(self):
        """Deleting a canonical workflow from WORKFLOW_ASKS resets registry to canonical default."""
        registry = get_workflow_registry()

        def custom_ask(question: str, **kwargs: Any) -> dict[str, Any]:
            return {"answer": "Custom"}

        WORKFLOW_ASKS["fix_single_llm"] = custom_ask
        assert isinstance(registry.get("fix_single_llm"), _LegacyUnifiedWorkflowAdapter)

        # Deleting entry must reset defaults
        del WORKFLOW_ASKS["fix_single_llm"]
        wf_restored = registry.get("fix_single_llm")
        assert isinstance(wf_restored, SelfCorrectiveRagWorkflow)
        assert not isinstance(wf_restored, _LegacyUnifiedWorkflowAdapter)
        assert WORKFLOW_ASKS.get("fix_single_llm") is fix_single_llm.ask

    def test_deleting_canonical_graph_resets_defaults(self):
        """Deleting a canonical workflow from WORKFLOW_GRAPHS resets registry to canonical default."""
        registry = get_workflow_registry()

        class DummyGraph:
            pass

        WORKFLOW_GRAPHS["fix_single_jev"] = DummyGraph()
        assert isinstance(registry.get("fix_single_jev"), _LegacyUnifiedWorkflowAdapter)

        del WORKFLOW_GRAPHS["fix_single_jev"]
        wf_restored = registry.get("fix_single_jev")
        assert isinstance(wf_restored, SelfCorrectiveRagWorkflow)
        assert WORKFLOW_GRAPHS.get("fix_single_jev") is fix_single_jev.agent_graph

    def test_custom_workflow_addition_and_deletion(self):
        """Adding a non-canonical workflow registers it, and deleting unregisters it."""
        registry = get_workflow_registry()
        custom_id = "temporary_custom_workflow"
        assert custom_id not in registry

        def custom_ask(question: str, **kwargs: Any) -> dict[str, Any]:
            return {"answer": f"Custom response: {question}"}

        WORKFLOW_ASKS[custom_id] = custom_ask
        assert custom_id in registry
        wf = registry.get(custom_id)
        assert isinstance(wf, _LegacyUnifiedWorkflowAdapter)

        runner = get_workflow_runner()
        resp = runner.execute(ChatRequest(workflow=custom_id, question="Custom ping"))
        assert resp.answer == "Custom response: Custom ping"

        # Deleting custom workflow unregisters it
        del WORKFLOW_ASKS[custom_id]
        assert custom_id not in registry
        with pytest.raises(KeyError):
            registry.get(custom_id)

    def test_dictionary_pop_and_update_synchronization(self):
        """Dict pop() and update() operations properly synchronize with registry."""
        registry = get_workflow_registry()
        temp_id = "sync_test_wf"

        def test_fn(question: str, **kwargs: Any) -> dict[str, Any]:
            return {"answer": "synced"}

        WORKFLOW_ASKS.update({temp_id: test_fn})
        assert temp_id in registry
        assert isinstance(registry.get(temp_id), _LegacyUnifiedWorkflowAdapter)

        popped = WORKFLOW_ASKS.pop(temp_id)
        assert popped is test_fn
        assert temp_id not in registry

    def test_routes_re_export_identity(self):
        """Verify that app.api.routes exports the exact same dictionary instances."""
        from app.api import routes
        from app.rag import workflows

        assert routes.WORKFLOW_ASKS is WORKFLOW_ASKS
        assert routes.WORKFLOW_GRAPHS is WORKFLOW_GRAPHS
        assert workflows.WORKFLOW_ASKS is WORKFLOW_ASKS
        assert workflows.WORKFLOW_GRAPHS is WORKFLOW_GRAPHS
