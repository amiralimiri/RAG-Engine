"""Unit, conformance, and multi-turn memory tests for ReActAgentWorkflow."""

from typing import Any
import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.api.schemas import ChatMessage, ChatRequest
from app.rag.workflows import get_workflow_registry
from app.rag.workflows.react_agent import ReActAgentWorkflow
from tests.rag.test_workflow_contracts import BaseWorkflowContractTest


class MockChatModel(SimpleChatModel):
    """Test model supporting tool-binding for LangGraph react agent tests."""

    @property
    def _llm_type(self) -> str:
        return "mock_chat_model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _call(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> str:
        last = messages[-1].content if messages else ""
        return f"Echo answer for: {last}"


@tool
def mock_dummy_tool(q: str) -> str:
    """A dummy test tool."""
    return f"Dummy search results for: {q}"


class TestReActAgentWorkflowContract(BaseWorkflowContractTest):
    """Verify ReActAgentWorkflow satisfies all architectural contracts."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        return ReActAgentWorkflow(
            id="test_react_agent",
            name="Test ReAct Agent",
            llm=MockChatModel(),
            tools=[mock_dummy_tool],
            checkpointer=MemorySaver(),
        )


class TestRegisteredReActAgentContract(BaseWorkflowContractTest):
    """Verify registered react_agent workflow satisfies contracts."""

    __test__ = True

    @pytest.fixture
    def workflow(self):
        reg = get_workflow_registry()
        return reg.get("react_agent")


def test_react_agent_to_initial_state_multi_turn():
    wf = ReActAgentWorkflow(llm=MockChatModel(), tools=[mock_dummy_tool])
    req = ChatRequest(
        question="What is the password policy?",
        messages=[
            ChatMessage(role="user", content="Hello IT support"),
            ChatMessage(role="assistant", content="Hello, how can I assist you?"),
        ],
        session_id="sess_abc",
    )
    state = wf.to_initial_state(req, trace_id="tid_xyz")
    messages = state["messages"]
    assert len(messages) == 3
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "Hello IT support"
    assert isinstance(messages[1], AIMessage)
    assert messages[1].content == "Hello, how can I assist you?"
    assert isinstance(messages[2], HumanMessage)
    assert messages[2].content == "What is the password policy?"
    assert state["session_id"] == "sess_abc"
    assert state["trace_id"] == "tid_xyz"


def test_react_agent_to_chat_response_citations_and_source():
    wf = ReActAgentWorkflow(llm=MockChatModel(), tools=[mock_dummy_tool])

    final_state = {
        "question": "How to set up VPN?",
        "messages": [
            HumanMessage(content="How to set up VPN?"),
            ToolMessage(
                content="[Source: /docs/network/vpn_setup.md]\nDownload OpenVPN client.",
                name="knowledge_base_search",
                tool_call_id="call_1",
            ),
            ToolMessage(
                content="Title: Official OpenVPN Community\nURL: https://openvpn.net\nContent: Client downloads.",
                name="web_search",
                tool_call_id="call_2",
            ),
            AIMessage(content="You can download OpenVPN following our internal guide."),
        ],
    }

    response = wf.to_chat_response(final_state, trace_id="trace_777")
    assert response.workflow == "react_agent"
    assert response.answer == "You can download OpenVPN following our internal guide."
    assert response.source_used == "react_agent"
    assert len(response.citations) == 2
    assert response.citations[0]["title"] == "vpn_setup.md"
    assert response.citations[0]["type"] == "private_kb"
    assert response.citations[1]["url"] == "https://openvpn.net"
    assert response.citations[1]["type"] == "web"
    assert response.trace_id == "trace_777"


def test_react_agent_multi_turn_memory_persistence():
    """Verify that MemorySaver persists conversation across turns using thread_id."""

    class MemoryEchoModel(SimpleChatModel):
        @property
        def _llm_type(self) -> str:
            return "memory_echo"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _call(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> str:
            user_inputs = [m.content for m in messages if isinstance(m, HumanMessage)]
            return f"Heard user inputs: {', '.join(user_inputs)}"

    checkpointer = MemorySaver()
    wf = ReActAgentWorkflow(
        id="memory_agent",
        llm=MemoryEchoModel(),
        tools=[mock_dummy_tool],
        checkpointer=checkpointer,
    )
    graph = wf.get_compiled_graph()

    session_thread = "session_turn_test_123"
    config = {"configurable": {"thread_id": session_thread}}

    # Turn 1
    req1 = ChatRequest(question="My name is Bob", session_id=session_thread)
    state1 = wf.to_initial_state(req1)
    res1 = graph.invoke({"messages": state1["messages"]}, config=config)
    resp1 = wf.to_chat_response(res1)
    assert "Heard user inputs: My name is Bob" in resp1.answer

    # Turn 2: Only pass new question, without resending "My name is Bob"
    req2 = ChatRequest(question="What is my name?", session_id=session_thread)
    state2 = wf.to_initial_state(req2)
    res2 = graph.invoke({"messages": state2["messages"]}, config=config)
    resp2 = wf.to_chat_response(res2)

    # Agent should recall previous turn
    assert "My name is Bob" in resp2.answer
    assert "What is my name?" in resp2.answer
    assert len(res2["messages"]) == 4  # Human1, AI1, Human2, AI2
