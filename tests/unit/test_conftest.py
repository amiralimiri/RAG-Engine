"""Unit tests verifying test isolation fixtures and MockWorkflowDefinition in tests/conftest.py."""

from unittest.mock import MagicMock

import pytest

from app.api.schemas import ChatRequest
from app.rag.workflows.base import EvalContext, WorkflowDefinition
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry
from tests.conftest import MockWorkflowDefinition


def test_mock_workflow_definition_satisfies_protocol():
    """MockWorkflowDefinition must implement the runtime_checkable WorkflowDefinition protocol."""
    mock_wf = MockWorkflowDefinition(id="test_proto")
    assert isinstance(mock_wf, WorkflowDefinition)
    assert mock_wf.info.id == "test_proto"
    assert mock_wf.info.name == "Mock Workflow"
    assert "retrieve_kb" in mock_wf.info.step_labels
    assert "generate_from_kb" in mock_wf.info.generation_nodes


def test_mock_workflow_definition_custom_configuration():
    """MockWorkflowDefinition accepts customized metadata, outputs, and citations."""
    custom_citations = [{"id": 1, "title": "Doc A", "url": "http://example.com"}]
    custom_contexts = ["Context snippet 1", "Context snippet 2"]
    custom_trace = ["retrieve", "evaluate", "synthesize"]

    mock_wf = MockWorkflowDefinition(
        id="custom_wf",
        name="Custom Workflow Double",
        description="Double for unit test",
        answer="Custom synthesized answer",
        source_used="hybrid",
        trace=custom_trace,
        citations=custom_citations,
        retrieved_contexts=custom_contexts,
        step_labels={"custom_node": "Custom step label"},
        generation_nodes={"custom_gen"},
        metadata={"version": "1.0"},
    )

    assert mock_wf.info.id == "custom_wf"
    assert mock_wf.info.name == "Custom Workflow Double"
    assert mock_wf.info.description == "Double for unit test"
    assert mock_wf.info.step_labels == {"custom_node": "Custom step label"}
    assert mock_wf.info.generation_nodes == {"custom_gen"}
    assert mock_wf.info.metadata == {"version": "1.0"}

    # State mappings
    req = ChatRequest(question="What is the test question?", workflow="custom_wf")
    init_state = mock_wf.to_initial_state(req, trace_id="trace-123")
    assert init_state["question"] == "What is the test question?"
    assert init_state["trace_id"] == "trace-123"

    resp = mock_wf.to_chat_response(
        {"answer": "Overridden answer", "source_used": "web"},
        trace_id="trace-123",
    )
    assert resp.workflow == "custom_wf"
    assert resp.answer == "Overridden answer"
    assert resp.source_used == "web"
    assert resp.trace == custom_trace
    assert resp.citations == custom_citations
    assert resp.trace_id == "trace-123"

    eval_ctx = mock_wf.extract_eval_context({})
    assert isinstance(eval_ctx, EvalContext)
    assert eval_ctx.source_used == "hybrid"
    assert eval_ctx.retrieved_contexts == custom_contexts

    err_state = mock_wf.handle_error(ValueError("Failed to generate"), {})
    assert "Failed to generate" in err_state["answer"]
    assert err_state["source_used"] == "direct"
    assert err_state["trace"] == ["error_fallback"]


@pytest.mark.asyncio
async def test_mock_workflow_graph_invoke_and_streaming():
    """MockWorkflowDefinition provides graph supporting invoke, ainvoke, and astream_events."""
    mock_wf = MockWorkflowDefinition(
        id="stream_wf",
        answer="Streaming test answer",
        source_used="knowledge_base",
    )

    graph = mock_wf.get_compiled_graph()
    invoke_res = graph.invoke({"question": "test"})
    assert invoke_res["answer"] == "Streaming test answer"
    assert invoke_res["source_used"] == "knowledge_base"

    ainvoke_res = await graph.ainvoke({"question": "test"})
    assert ainvoke_res["answer"] == "Streaming test answer"

    # Default astream_events v2
    streamed_events = []
    async for event in graph.astream_events({"question": "test"}, version="v2"):
        streamed_events.append(event)

    assert len(streamed_events) == 1
    assert streamed_events[0]["event"] == "on_chat_model_stream"
    assert streamed_events[0]["metadata"]["langgraph_node"] == "generate_from_kb"
    assert streamed_events[0]["data"]["chunk"].content == "Streaming test answer"


@pytest.mark.asyncio
async def test_mock_workflow_custom_graph_and_custom_stream_events():
    """MockWorkflowDefinition supports injected custom graphs and custom stream event sequences."""
    custom_graph = MagicMock()
    custom_graph.invoke.return_value = {"answer": "Injected graph answer"}

    wf_custom_graph = MockWorkflowDefinition(id="custom_graph_wf", graph=custom_graph)
    assert wf_custom_graph.get_compiled_graph() is custom_graph

    # Custom stream events generator
    custom_events = [
        {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "node1"}, "data": {"chunk": "tok1"}},
        {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "node2"}, "data": {"chunk": "tok2"}},
    ]
    wf_custom_stream = MockWorkflowDefinition(id="custom_stream_wf", stream_events=custom_events)
    streamed = []
    async for event in wf_custom_stream.get_compiled_graph().astream_events({}, version="v2"):
        streamed.append(event)
    assert streamed == custom_events


def test_clean_registry_provides_canonical_defaults(clean_registry: WorkflowRegistry):
    """The clean_registry fixture provides a reset registry with default workflows."""
    registered_ids = clean_registry.list_ids()
    assert "fix_single_llm" in registered_ids
    assert "fix_single_jev" in registered_ids
    assert "react_agent" in registered_ids
    assert clean_registry is get_workflow_registry()


def test_clean_registry_isolation_part_1(clean_registry: WorkflowRegistry):
    """Mutating registry in part 1 must not pollute part 2."""
    clean_registry.register(MockWorkflowDefinition(id="isolated_wf_leak_test"))
    assert "isolated_wf_leak_test" in clean_registry


def test_clean_registry_isolation_part_2(clean_registry: WorkflowRegistry):
    """Part 2 must see canonical defaults and not see mutations from part 1."""
    assert "isolated_wf_leak_test" not in clean_registry
    assert "fix_single_llm" in clean_registry
    assert "fix_single_jev" in clean_registry
    assert "react_agent" in clean_registry


def test_register_mock_workflow_fixture(register_mock_workflow, clean_registry: WorkflowRegistry):
    """The register_mock_workflow fixture helper creates and registers test doubles cleanly."""
    mock_wf = register_mock_workflow("helper_wf", answer="Helper answer", source_used="web")
    assert isinstance(mock_wf, MockWorkflowDefinition)
    assert mock_wf.info.id == "helper_wf"
    assert clean_registry.get("helper_wf") is mock_wf
    assert clean_registry.get("helper_wf").answer == "Helper answer"

    # Can override canonical default with helper
    override_llm = register_mock_workflow("fix_single_llm", answer="Mock LLM response")
    assert clean_registry.get("fix_single_llm") is override_llm
    assert clean_registry.get("fix_single_llm").answer == "Mock LLM response"
