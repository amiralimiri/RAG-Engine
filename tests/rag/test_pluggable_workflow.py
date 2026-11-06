"""Tests for StandardWorkflowState and PluggableWorkflow adapter."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.rag.workflows.base import (
    EvalContext,
    PluggableWorkflow,
    StandardWorkflowState,
    WorkflowDefinition,
    WorkflowInfo,
    WorkflowInput,
    WorkflowOutput,
)
from tests.rag.test_workflow_contracts import BaseWorkflowContractTest


class TestPluggableWorkflowSimpleStateGraph(BaseWorkflowContractTest):
    """Verifies that PluggableWorkflow wrapping a simple StateGraph passes conformance contracts."""

    __test__ = True

    @pytest.fixture
    def workflow(self) -> PluggableWorkflow:
        info = WorkflowInfo(
            id="simple_pluggable_wf",
            name="Simple Pluggable Workflow",
            description="Pluggable workflow wrapping a standard StateGraph.",
            is_conversational=False,
            supports_streaming=True,
            step_labels={"retrieve": "Searching knowledge base", "generate": "Synthesizing answer"},
            generation_nodes={"generate"},
        )

        builder = StateGraph(StandardWorkflowState)

        def retrieve_node(state: StandardWorkflowState) -> dict[str, Any]:
            return {
                "retrieved_contexts": ["Context passage 1", "Context passage 2"],
                "source_used": "knowledge_base",
                "trace": ["Retrieved context from vectorstore"],
            }

        def generate_node(state: StandardWorkflowState) -> dict[str, Any]:
            existing_trace = list(state.get("trace") or [])
            return {
                "answer": "Synthesized knowledge base response.",
                "trace": existing_trace + ["Generated response"],
                "citations": [{"title": "Knowledge Doc", "url": "https://kb.example.com"}],
            }

        builder.add_node("retrieve", retrieve_node)
        builder.add_node("generate", generate_node)
        builder.add_edge(START, "retrieve")
        builder.add_edge("retrieve", "generate")
        builder.add_edge("generate", END)

        return PluggableWorkflow(info=info, graph=builder.compile())


class TestPluggableWorkflowMessageGraph(BaseWorkflowContractTest):
    """Verifies that PluggableWorkflow wrapping a conversational message-based graph passes contracts."""

    __test__ = True

    @pytest.fixture
    def workflow(self) -> PluggableWorkflow:
        info = WorkflowInfo(
            id="message_pluggable_wf",
            name="Message Pluggable Workflow",
            description="Pluggable workflow wrapping a message-based conversational agent.",
            is_conversational=True,
            supports_streaming=True,
            step_labels={"agent": "Thinking and replying"},
            generation_nodes={"agent"},
        )

        builder = StateGraph(StandardWorkflowState)

        def agent_node(state: StandardWorkflowState) -> dict[str, Any]:
            msgs = list(state.get("messages") or [])
            msgs.append(AIMessage(content="Conversational AI answer from trailing message."))
            return {
                "messages": msgs,
                "source_used": "direct",
                "trace": ["Agent reasoned and responded"],
            }

        builder.add_node("agent", agent_node)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)

        return PluggableWorkflow(info=info, graph=builder.compile())


class TestPluggableWorkflowMockFactory(BaseWorkflowContractTest):
    """Verifies that PluggableWorkflow initialized with a lazy factory callable passes contracts."""

    __test__ = True

    @pytest.fixture
    def workflow(self) -> PluggableWorkflow:
        info = WorkflowInfo(
            id="factory_pluggable_wf",
            name="Factory Pluggable Workflow",
            description="Pluggable workflow initialized via a lazy factory function.",
            is_conversational=False,
            supports_streaming=True,
            step_labels={"mock_node": "Mock processing"},
            generation_nodes={"mock_node"},
        )

        class MockRunnable:
            def invoke(self, state: Any, config: Any = None) -> dict[str, Any]:
                return {
                    "answer": "Mock output answer",
                    "source_used": "mock_engine",
                    "trace": ["Mock execution trace"],
                }

            async def ainvoke(self, state: Any, config: Any = None) -> dict[str, Any]:
                return self.invoke(state, config)

        factory_call_count = {"count": 0}

        def factory() -> Any:
            factory_call_count["count"] += 1
            return MockRunnable()

        wf = PluggableWorkflow(info=info, graph=factory)
        wf._factory_call_count = factory_call_count  # type: ignore[attr-defined]
        return wf


def test_pluggable_workflow_protocol_conformance():
    """Verify PluggableWorkflow satisfies runtime_checkable WorkflowDefinition protocol."""
    info = WorkflowInfo(
        id="proto_check",
        name="Proto Check",
        description="Protocol conformance test",
        supports_streaming=False,
    )
    wf = PluggableWorkflow(info=info, graph=lambda: None)
    assert isinstance(wf, WorkflowDefinition)


def test_answer_extraction_from_direct_string():
    """Verify answer is directly extracted from state['answer'] string."""
    info = WorkflowInfo(id="test_wf", name="Test", description="Test", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    state = {
        "answer": "Direct string answer",
        "messages": [AIMessage(content="Trailing message should not override explicit answer")],
    }
    output = wf.to_workflow_output(state)
    assert output.answer == "Direct string answer"


def test_answer_extraction_from_aimessage_in_answer():
    """Verify answer is extracted when state['answer'] is an AIMessage."""
    info = WorkflowInfo(id="test_wf", name="Test", description="Test", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    state = {
        "answer": AIMessage(content="Extracted from AIMessage in answer key"),
    }
    output = wf.to_workflow_output(state)
    assert output.answer == "Extracted from AIMessage in answer key"


def test_answer_extraction_from_trailing_aimessage():
    """Verify answer falls back to trailing AIMessage in messages when answer key is empty."""
    info = WorkflowInfo(id="test_wf", name="Test", description="Test", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    state = {
        "answer": "",
        "messages": [
            HumanMessage(content="Hello"),
            AIMessage(content="Hello! How can I assist you today?"),
        ],
    }
    output = wf.to_workflow_output(state)
    assert output.answer == "Hello! How can I assist you today?"


def test_answer_extraction_from_assistant_dict_message():
    """Verify answer falls back to trailing assistant dictionary message."""
    info = WorkflowInfo(id="test_wf", name="Test", description="Test", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    state = {
        "messages": [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer from dict message"},
        ],
    }
    output = wf.to_workflow_output(state)
    assert output.answer == "Answer from dict message"


def test_answer_extraction_from_multipart_content_blocks():
    """Verify content block lists in AIMessage are joined into a continuous string."""
    info = WorkflowInfo(id="test_wf", name="Test", description="Test", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    state = {
        "answer": "",
        "messages": [
            AIMessage(
                content=[
                    {"type": "text", "text": "First chunk. "},
                    {"type": "text", "text": "Second chunk."},
                ]
            )
        ],
    }
    output = wf.to_workflow_output(state)
    assert output.answer == "First chunk. Second chunk."


def test_answer_extraction_empty_fallback():
    """Verify empty string is returned when no answer or messages are available."""
    info = WorkflowInfo(id="test_wf", name="Test", description="Test", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    output = wf.to_workflow_output({})
    assert output.answer == ""


def test_to_initial_state_normalization():
    """Verify WorkflowInput is correctly transformed into StandardWorkflowState."""
    info = WorkflowInfo(id="norm_wf", name="Norm", description="Norm", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    inp = WorkflowInput(
        question="What is LangGraph?",
        messages=[{"role": "system", "content": "You are helpful."}],
        attachments=[{"type": "pdf", "file_name": "manual.pdf"}],
        session_id="sess_123",
        trace_id="trace_456",
        variant_id="gpt-4o-mini",
    )

    init_state = wf.to_initial_state(inp)
    assert init_state["question"] == "What is LangGraph?"
    assert init_state["session_id"] == "sess_123"
    assert init_state["trace_id"] == "trace_456"
    assert init_state["variant_id"] == "gpt-4o-mini"
    assert len(init_state["attachments"]) == 1
    assert init_state["attachments"][0]["file_name"] == "manual.pdf"

    messages = init_state["messages"]
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "You are helpful."
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "What is LangGraph?"


def test_to_initial_state_infers_question_from_messages_when_empty():
    """Verify question is inferred from the last HumanMessage when WorkflowInput.question is empty."""
    info = WorkflowInfo(id="infer_wf", name="Infer", description="Infer", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    inp = WorkflowInput(
        question="",
        messages=[
            {"role": "user", "content": "Inferred question from history"},
        ],
    )
    init_state = wf.to_initial_state(inp)
    assert init_state["question"] == "Inferred question from history"


def test_rewritten_query_resolution():
    """Verify rewritten_query resolves from rewritten_query, question, or messages."""
    info = WorkflowInfo(id="query_wf", name="Query", description="Query", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    # 1. Explicit rewritten_query
    out1 = wf.to_workflow_output({"rewritten_query": "better query", "question": "orig"})
    assert out1.rewritten_query == "better query"

    # 2. Falls back to question
    out2 = wf.to_workflow_output({"question": "orig query"})
    assert out2.rewritten_query == "orig query"

    # 3. Falls back to HumanMessage
    out3 = wf.to_workflow_output({"messages": [HumanMessage(content="human query")]})
    assert out3.rewritten_query == "human query"


def test_extract_eval_context():
    """Verify extract_eval_context extracts evidence contexts and source_used."""
    info = WorkflowInfo(id="eval_wf", name="Eval", description="Eval", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    # Direct fields
    ctx1 = wf.extract_eval_context(
        {
            "source_used": "vectorstore",
            "retrieved_contexts": ["Snippet 1", "Snippet 2"],
        }
    )
    assert isinstance(ctx1, EvalContext)
    assert ctx1.source_used == "vectorstore"
    assert ctx1.retrieved_contexts == ["Snippet 1", "Snippet 2"]

    # Fallback to tool messages
    ctx2 = wf.extract_eval_context(
        {
            "source_used": "tools",
            "messages": [
                ToolMessage(content="Retrieved tool result chunk", tool_call_id="call_1"),
            ],
        }
    )
    assert isinstance(ctx2, EvalContext)
    assert ctx2.source_used == "tools"
    assert ctx2.retrieved_contexts == ["Retrieved tool result chunk"]


def test_handle_error_produces_graceful_fallback():
    """Verify handle_error synthesizes fallback state converting into valid WorkflowOutput."""
    info = WorkflowInfo(id="err_wf", name="Err", description="Err", supports_streaming=False)
    wf = PluggableWorkflow(info=info, graph=lambda: None)

    current_state = {
        "question": "What happened?",
        "trace": ["Step 1 started"],
    }
    exc = ValueError("Database connection lost")
    fallback = wf.handle_error(exc, current_state)

    assert fallback["question"] == "What happened?"
    assert fallback["source_used"] == "error_fallback"
    assert any("Database connection lost" in t for t in fallback["trace"])

    output = wf.to_workflow_output(fallback, trace_id="trace_err_99")
    assert isinstance(output, WorkflowOutput)
    assert output.workflow == "err_wf"
    assert "unexpected issue" in output.answer.lower()
    assert output.source_used == "error_fallback"
    assert output.trace_id == "trace_err_99"
    assert any("Workflow error fallback" in t for t in output.trace)


def test_lazy_graph_compilation_and_caching():
    """Verify factory is executed once and cached singleton is returned."""
    info = WorkflowInfo(id="cache_wf", name="Cache", description="Cache", supports_streaming=False)

    call_count = 0

    class DummyGraph:
        def invoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

    def factory() -> DummyGraph:
        nonlocal call_count
        call_count += 1
        return DummyGraph()

    wf = PluggableWorkflow(info=info, graph=factory)
    assert call_count == 0

    g1 = wf.get_compiled_graph()
    assert call_count == 1

    g2 = wf.get_compiled_graph()
    assert call_count == 1
    assert g1 is g2


def test_end_to_end_pluggable_execution():
    """Verify end-to-end flow: WorkflowInput -> initial_state -> invoke -> WorkflowOutput."""
    info = WorkflowInfo(
        id="e2e_wf",
        name="E2E Workflow",
        description="End to end test",
        step_labels={"node": "Execute"},
        generation_nodes={"node"},
    )

    builder = StateGraph(StandardWorkflowState)

    def node_fn(state: StandardWorkflowState) -> dict[str, Any]:
        return {
            "answer": f"Processed question: {state.get('question')}",
            "source_used": "knowledge_base",
            "retrieved_contexts": ["KB doc section 4"],
            "citations": [{"title": "KB Doc 4", "url": "https://example.com/4"}],
            "trace": ["Node executed successfully"],
        }

    builder.add_node("node", node_fn)
    builder.add_edge(START, "node")
    builder.add_edge("node", END)

    wf = PluggableWorkflow(info=info, graph=builder.compile())

    inp = WorkflowInput(question="How does billing work?", session_id="s1")
    init_state = wf.to_initial_state(inp, trace_id="t1")

    graph = wf.get_compiled_graph()
    final_state = graph.invoke(init_state)

    output = wf.to_workflow_output(final_state, trace_id="t1")
    assert output.workflow == "e2e_wf"
    assert output.answer == "Processed question: How does billing work?"
    assert output.source_used == "knowledge_base"
    assert len(output.citations) == 1
    assert output.citations[0].title == "KB Doc 4"
    assert output.trace == ["Node executed successfully"]
    assert output.trace_id == "t1"

    eval_ctx = wf.extract_eval_context(final_state)
    assert eval_ctx.source_used == "knowledge_base"
    assert eval_ctx.retrieved_contexts == ["KB doc section 4"]
