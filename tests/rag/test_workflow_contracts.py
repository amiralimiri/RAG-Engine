"""Reusable conformance test suite for WorkflowDefinition implementations."""

from typing import Any
import pytest
from langgraph.graph import END, START, StateGraph

from app.api.schemas import ChatRequest, ChatResponse
from app.rag.workflows.base import (
    EvalContext,
    WorkflowDefinition,
    WorkflowInfo,
)


class BaseWorkflowContractTest:
    """Base test suite verifying that a workflow satisfies all architectural contracts.

    Subclasses should define a pytest fixture `workflow` returning the WorkflowDefinition instance.
    """

    __test__ = False  # Prevent pytest from running this base class directly

    def test_workflow_info_validity(self, workflow: WorkflowDefinition):
        assert isinstance(workflow, WorkflowDefinition), "Workflow must satisfy WorkflowDefinition protocol"
        assert isinstance(workflow.info, WorkflowInfo), "Workflow must have a valid WorkflowInfo object"
        assert workflow.info.id.strip(), "Workflow id cannot be empty"
        assert workflow.info.name.strip(), "Workflow name cannot be empty"
        assert workflow.info.description.strip(), "Workflow description cannot be empty"
        assert isinstance(workflow.info.step_labels, dict), "step_labels must be a dict"
        assert isinstance(workflow.info.generation_nodes, set), "generation_nodes must be a set"
        if workflow.info.supports_streaming:
            assert len(workflow.info.generation_nodes) > 0, (
                "Workflows supporting streaming must declare at least one generation node in generation_nodes"
            )

    def test_to_initial_state_contract(self, workflow: WorkflowDefinition):
        request = ChatRequest(
            question="How do I setup company VPN?",
            session_id="test_sess_001",
            user_id="user_123",
        )
        state = workflow.to_initial_state(request, trace_id="trace_xyz")
        assert isinstance(state, dict), "to_initial_state must return a dict"
        # Check that query content is preserved in state
        serialized_state = str(state)
        assert "How do I setup company VPN?" in serialized_state, (
            "Inbound question must be preserved in internal initial state"
        )

    def test_to_chat_response_contract(self, workflow: WorkflowDefinition):
        request = ChatRequest(question="Test question")
        init_state = workflow.to_initial_state(request, trace_id="trace_abc")
        # Ensure minimal answer fields exist for state conversion
        test_state = dict(init_state)
        if not test_state.get("answer"):
            test_state["answer"] = "Here is your test answer."
        if not test_state.get("source_used"):
            test_state["source_used"] = "test_source"
        if not test_state.get("citations"):
            test_state["citations"] = [{"title": "Doc 1", "url": "https://example.com"}]
        if not test_state.get("trace"):
            test_state["trace"] = ["Step 1", "Step 2"]

        response = workflow.to_chat_response(test_state, trace_id="trace_abc")
        assert isinstance(response, ChatResponse), "to_chat_response must return a ChatResponse instance"
        assert response.workflow == workflow.info.id, "ChatResponse workflow must match workflow.info.id"
        assert response.answer, "ChatResponse answer must not be empty"
        assert response.source_used, "ChatResponse source_used must be declared"
        assert isinstance(response.citations, list), "ChatResponse citations must be a list"
        assert isinstance(response.trace, list), "ChatResponse trace must be a list"
        assert response.trace_id == "trace_abc", "ChatResponse trace_id must match passed trace_id"

    def test_extract_eval_context_contract(self, workflow: WorkflowDefinition):
        request = ChatRequest(question="Test question")
        init_state = workflow.to_initial_state(request)
        test_state = dict(init_state)
        if not test_state.get("source_used"):
            test_state["source_used"] = "vectorstore"
        if not test_state.get("retrieved_contexts"):
            test_state["retrieved_contexts"] = ["Context paragraph 1", "Context paragraph 2"]

        eval_ctx = workflow.extract_eval_context(test_state)
        assert isinstance(eval_ctx, EvalContext), "extract_eval_context must return an EvalContext"
        assert isinstance(eval_ctx.source_used, str), "EvalContext source_used must be a string"
        assert isinstance(eval_ctx.retrieved_contexts, list), "EvalContext retrieved_contexts must be a list"
        assert all(isinstance(c, str) for c in eval_ctx.retrieved_contexts), (
            "All retrieved_contexts entries must be strings"
        )

    def test_handle_error_fallback(self, workflow: WorkflowDefinition):
        exc = RuntimeError("Simulated upstream service outage")
        fallback_state = workflow.handle_error(exc, current_state={})
        assert isinstance(fallback_state, dict), "handle_error must return a dict state"
        response = workflow.to_chat_response(fallback_state, trace_id="err_trace")
        assert isinstance(response, ChatResponse), "Fallback state must convert into a valid ChatResponse"
        assert response.answer, "Fallback ChatResponse must contain a user-facing explanation"
        assert "error" in response.source_used.lower() or response.source_used == "insufficient_evidence", (
            f"Fallback source_used should indicate an error condition, got: '{response.source_used}'"
        )

    def test_compiled_graph_is_runnable(self, workflow: WorkflowDefinition):
        graph = workflow.get_compiled_graph()
        assert graph is not None, "get_compiled_graph() must not return None"
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke"), (
            "Compiled graph must have invoke or ainvoke methods"
        )
        # Verify lazy compilation caching returns identical runnable
        second_graph = workflow.get_compiled_graph()
        assert graph is second_graph, "Subsequent calls to get_compiled_graph() must return cached singleton"


class FakeWorkflowState(dict):
    pass


class FakeWorkflow:
    """A clean, hermetic test double satisfying WorkflowDefinition."""

    def __init__(self) -> None:
        self.info = WorkflowInfo(
            id="fake_workflow",
            name="Fake Workflow",
            description="Hermetic test double for workflow conformance testing",
            is_conversational=False,
            supports_streaming=True,
            step_labels={"node_1": "Executing node 1", "generate": "Generating answer"},
            generation_nodes={"generate"},
        )
        self._compiled_graph = None

    def get_compiled_graph(self) -> Any:
        if self._compiled_graph is None:
            builder = StateGraph(dict)
            builder.add_node("node_1", lambda state: {"step": "node_1"})
            builder.add_node("generate", lambda state: {"answer": "Fake answer", "source_used": "fake_kb"})
            builder.add_edge(START, "node_1")
            builder.add_edge("node_1", "generate")
            builder.add_edge("generate", END)
            self._compiled_graph = builder.compile()
        return self._compiled_graph

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        return {
            "question": request.question,
            "session_id": request.session_id,
            "trace_id": trace_id,
            "answer": "",
            "source_used": "",
            "citations": [],
            "trace": [],
            "retrieved_contexts": [],
        }

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        return ChatResponse(
            workflow=self.info.id,
            answer=final_state.get("answer") or "Fallback answer",
            source_used=final_state.get("source_used") or "direct",
            citations=final_state.get("citations") or [],
            trace=final_state.get("trace") or [],
            trace_id=trace_id,
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used", "direct"),
            retrieved_contexts=final_state.get("retrieved_contexts", []),
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": "An unexpected error occurred while processing your request.",
            "source_used": "error_fallback",
            "trace": [f"Error: {str(exc)}"],
            "citations": [],
            "retrieved_contexts": [],
        }


class TestFakeWorkflowContract(BaseWorkflowContractTest):
    """Verifies that FakeWorkflow cleanly satisfies the BaseWorkflowContractTest suite."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        return FakeWorkflow()


def test_contract_failure_on_invalid_workflow():
    """Demonstrate that BaseWorkflowContractTest assertions catch broken workflow implementations."""
    base_test = BaseWorkflowContractTest()

    class BrokenWorkflow:
        info = WorkflowInfo(
            id="broken",
            name="Broken",
            description="Broken workflow",
            supports_streaming=True,
            generation_nodes=set(),  # Broken: streaming=True but empty generation_nodes
        )

    with pytest.raises(AssertionError, match="must satisfy WorkflowDefinition protocol"):
        base_test.test_workflow_info_validity(BrokenWorkflow())
