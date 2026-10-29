"""Conformance tests for SelfCorrectiveRagWorkflow implementations."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.documents import Document
from langchain_core.tools import BaseTool

from app.rag.classifiers import FakeClassifierAdapter
from app.rag.workflows import get_workflow_registry
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow
from tests.rag.test_workflow_contracts import BaseWorkflowContractTest


class TestSelfCorrectiveRagFakeClassifierContract(BaseWorkflowContractTest):
    """Verify contract adherence using FakeClassifierAdapter and mock retriever."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        mock_kb = MagicMock(spec=BaseTool)
        mock_kb.invoke.return_value = ("content", {})
        return SelfCorrectiveRagWorkflow(
            id="self_corrective_test",
            name="Self Corrective Test",
            classifier=FakeClassifierAdapter(route_decision="kb", kb_grade="good"),
            kb_tool=mock_kb,
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
    wf = SelfCorrectiveRagWorkflow(
        id="test_sc",
        name="Test",
        classifier=FakeClassifierAdapter(),
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
    wf = SelfCorrectiveRagWorkflow(
        id="test_sc",
        name="Test",
        classifier=FakeClassifierAdapter(),
    )
    current_state = {"trace": ["Router -> KB"], "question": "test"}
    fallback = wf.handle_error(ValueError("Vectorstore down"), current_state)

    assert fallback["source_used"] == "error_fallback"
    assert "Workflow error fallback: Vectorstore down" in fallback["trace"]
    assert "unexpected issue" in fallback["answer"].lower()


class TestSelfCorrectiveRagCustomToolsContract(BaseWorkflowContractTest):
    """Verify contract adherence when custom kb_tool and web_tool are injected."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        kb_tool = MagicMock(spec=BaseTool)
        kb_tool.name = "knowledge_base_search"
        web_tool = MagicMock(spec=BaseTool)
        web_tool.name = "web_search"
        return SelfCorrectiveRagWorkflow(
            id="self_corrective_custom_tools",
            name="Self Corrective Custom Tools",
            classifier=FakeClassifierAdapter(route_decision="kb", kb_grade="good"),
            kb_tool=kb_tool,
            web_tool=web_tool,
        )


def test_self_corrective_tool_injection_parameters():
    """Verify SelfCorrectiveRagWorkflow accepts and stores kb_tool and web_tool."""
    mock_kb_tool = MagicMock(spec=BaseTool)
    mock_web_tool = MagicMock(spec=BaseTool)
    wf_default = SelfCorrectiveRagWorkflow(
        id="test_default_tools",
        name="Test Default",
        classifier=FakeClassifierAdapter(),
    )
    assert wf_default.kb_tool is None
    assert wf_default.web_tool is None

    wf_injected = SelfCorrectiveRagWorkflow(
        id="test_injected_tools",
        name="Test Injected",
        classifier=FakeClassifierAdapter(),
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )
    assert wf_injected.kb_tool is mock_kb_tool
    assert wf_injected.web_tool is mock_web_tool


def test_self_corrective_get_compiled_graph_forwards_tools():
    """Verify get_compiled_graph forwards injected kb_tool and web_tool to create_agentic_rag_graph."""
    mock_kb_tool = MagicMock(spec=BaseTool)
    mock_web_tool = MagicMock(spec=BaseTool)
    classifier = FakeClassifierAdapter()
    mock_retriever = MagicMock()

    wf = SelfCorrectiveRagWorkflow(
        id="test_forwarding",
        name="Test Forwarding",
        classifier=classifier,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    with patch("app.rag.workflows.self_corrective.create_agentic_rag_graph") as mock_create_graph:
        mock_create_graph.return_value = MagicMock()
        graph = wf.get_compiled_graph()
        mock_create_graph.assert_called_once_with(
            classifier=classifier,
            model=None,
            kb_tool=mock_kb_tool,
            web_tool=mock_web_tool,
        )
        assert graph is mock_create_graph.return_value


def test_self_corrective_model_injection_parameters():
    """Verify SelfCorrectiveRagWorkflow accepts, stores, and forwards model."""
    from langchain_core.language_models.chat_models import BaseChatModel
    mock_model = MagicMock(spec=BaseChatModel)
    classifier = FakeClassifierAdapter()
    wf = SelfCorrectiveRagWorkflow(
        id="test_model_fwd",
        name="Test Model Fwd",
        classifier=classifier,
        model=mock_model,
    )
    assert wf.model is mock_model

    with patch("app.rag.workflows.self_corrective.create_agentic_rag_graph") as mock_create_graph:
        mock_create_graph.return_value = MagicMock()
        wf.get_compiled_graph()
        mock_create_graph.assert_called_once_with(
            classifier=classifier,
            model=mock_model,
            kb_tool=None,
            web_tool=None,
        )


@pytest.mark.asyncio
async def test_self_corrective_workflow_execution_with_injected_tools():
    """Verify that graph compiled from workflow executes using injected tools."""
    mock_kb_tool = MagicMock(spec=BaseTool)
    custom_doc = Document(page_content="Injected workflow KB doc", metadata={"source": "workflow/kb.md"})
    mock_kb_tool.ainvoke = AsyncMock(
        return_value=(
            "[Source: workflow/kb.md]\nInjected workflow KB doc",
            {
                "documents": [custom_doc],
                "citations": [{"title": "kb.md", "url": "", "type": "private_kb"}],
            },
        )
    )

    mock_web_tool = MagicMock(spec=BaseTool)
    mock_web_tool.ainvoke = AsyncMock(return_value=("web snippet", {}))

    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")
    wf = SelfCorrectiveRagWorkflow(
        id="test_exec",
        name="Test Exec",
        classifier=classifier,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    graph = wf.get_compiled_graph()
    result = await graph.ainvoke({"question": "How to configure VPN?", "retry_count": 0})
    mock_kb_tool.ainvoke.assert_awaited_once()
    assert result["kb_docs"] == [custom_doc]
    assert result["citations"] == [{"title": "kb.md", "url": "", "type": "private_kb"}]

