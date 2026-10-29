"""Conformance tests for SelfCorrectiveRagWorkflow implementations."""

from unittest.mock import MagicMock
import pytest

from app.rag.classifiers import FakeClassifierAdapter
from app.rag.workflows import get_workflow_registry
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow
from tests.rag.test_workflow_contracts import BaseWorkflowContractTest


class TestSelfCorrectiveRagFakeClassifierContract(BaseWorkflowContractTest):
    """Verify contract adherence using FakeClassifierAdapter and mock retriever."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        mock_retriever = MagicMock()
        mock_retriever.invoke.return_value = []
        return SelfCorrectiveRagWorkflow(
            id="self_corrective_test",
            name="Self Corrective Test",
            classifier=FakeClassifierAdapter(route_decision="kb", kb_grade="good"),
            retriever=mock_retriever,
        )


class TestRegisteredLlmWorkflowContract(BaseWorkflowContractTest):
    """Verify contract adherence for registered fix_single_llm workflow."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        registry = get_workflow_registry()
        return registry.get("fix_single_llm")


class TestRegisteredJevWorkflowContract(BaseWorkflowContractTest):
    """Verify contract adherence for registered fix_single_jev workflow."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        registry = get_workflow_registry()
        return registry.get("fix_single_jev")


def test_self_corrective_state_mutation():
    """Verify initial state keys match AgentState expectation."""
    mock_retriever = MagicMock()
    wf = SelfCorrectiveRagWorkflow(
        id="test_sc",
        name="Test",
        classifier=FakeClassifierAdapter(),
        retriever=mock_retriever,
    )
    from app.api.schemas import ChatRequest
    req = ChatRequest(question="How to configure VPN?", trace_id="tid_123")
    state = wf.to_initial_state(req)

    assert state["question"] == "How to configure VPN?"
    assert state["retry_count"] == 0
    assert state["trace_id"] == "tid_123"
    assert isinstance(state["kb_docs"], list)
    assert isinstance(state["citations"], list)


def test_self_corrective_handle_error_trace():
    """Verify error handler appends error to trace and returns user-friendly fallback."""
    mock_retriever = MagicMock()
    wf = SelfCorrectiveRagWorkflow(
        id="test_sc",
        name="Test",
        classifier=FakeClassifierAdapter(),
        retriever=mock_retriever,
    )
    current_state = {"trace": ["Router -> KB"], "question": "test"}
    fallback = wf.handle_error(ValueError("Vectorstore down"), current_state)

    assert fallback["source_used"] == "error_fallback"
    assert "Workflow error fallback: Vectorstore down" in fallback["trace"]
    assert "unexpected issue" in fallback["answer"].lower()
