from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.rag.classifiers import FakeClassifierAdapter
from app.rag.engine import create_agentic_rag_graph
from app.rag.workflows.base import WorkflowInput
from app.rag.workflows.react_agent import ReActAgentWorkflow
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow
from tests.rag.test_react_agent import MockChatModel


@pytest.fixture
def mock_llm():
    mock = MagicMock(spec=BaseChatModel)

    async def mock_ainvoke(prompt, **kwargs):
        prompt_str = str(prompt)
        if "rephrase the follow up question" in prompt_str.lower() or "standalone search query" in prompt_str.lower():
            return AIMessage(content="How do I install Cisco AnyConnect on Mac?")
        if "search quer" in prompt_str.lower() or "rewrite" in prompt_str.lower():
            return AIMessage(content="rewritten query text")
        return AIMessage(content="Generated response")

    mock.ainvoke = AsyncMock(side_effect=mock_ainvoke)
    mock.invoke = MagicMock(return_value=AIMessage(content="Generated response"))
    return mock


@pytest.fixture
def fake_retriever():
    mock_ret = MagicMock()
    mock_ret.invoke.return_value = [
        Document(
            page_content="Private company VPN policy details",
            metadata={"source": "docs/it/vpn_policy.md"},
        )
    ]
    return mock_ret


@pytest.fixture
def mock_kb_tool(fake_retriever):
    from app.rag.tools import create_retriever_tool
    return create_retriever_tool(retriever=fake_retriever)


@pytest.fixture
def mock_web_tool():
    from app.rag.tools import create_web_search_tool
    mock_client = MagicMock()
    mock_client.invoke.return_value = {
        "answer": "Web answer content",
        "results": [],
    }
    return create_web_search_tool(search_client=mock_client)


def test_turn_1_bypasses_condense_query(mock_llm, mock_kb_tool, mock_web_tool):
    classifier = FakeClassifierAdapter(route_decision="direct")
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    initial_state = {
        "question": "What is the VPN policy?",
        "current_query": "What is the VPN policy?",
        "messages": [HumanMessage(content="What is the VPN policy?")],
        "retry_count": 0,
    }

    result = graph.invoke(initial_state)

    trace = result.get("trace", [])
    assert not any("condense_query" in t.lower() or "contextualized" in t.lower() for t in trace)
    assert result["current_query"] == "What is the VPN policy?"
    assert result["question"] == "What is the VPN policy?"


def test_turn_2_triggers_condense_query(mock_llm, mock_kb_tool, mock_web_tool):
    classifier = FakeClassifierAdapter(route_decision="direct")
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    initial_state = {
        "question": "How do I install it on Mac?",
        "current_query": "How do I install it on Mac?",
        "messages": [
            HumanMessage(content="What is the VPN policy?"),
            AIMessage(content="You should use Cisco AnyConnect."),
            HumanMessage(content="How do I install it on Mac?"),
        ],
        "retry_count": 0,
    }

    result = graph.invoke(initial_state)

    trace = result.get("trace", [])
    assert any("contextualized" in t.lower() for t in trace)
    assert result["current_query"] == "How do I install Cisco AnyConnect on Mac?"
    assert result["question"] == "How do I install it on Mac?"


def test_transient_state_scrubbing_between_turns(mock_kb_tool):
    wf = SelfCorrectiveRagWorkflow(
        id="test_sc",
        name="Test Self Corrective",
        classifier=FakeClassifierAdapter(route_decision="kb", kb_grade="good"),
        kb_tool=mock_kb_tool,
    )

    turn1_input = WorkflowInput(question="What is the VPN policy?")
    turn1_state = wf.to_initial_state(turn1_input)

    # Simulate Turn 1 populating transient artifacts
    turn1_state["kb_docs"] = [Document(page_content="Cisco AnyConnect info")]
    turn1_state["web_results"] = "some external docs"
    turn1_state["kb_grade"] = "good"
    turn1_state["web_grade"] = "weak"
    turn1_state["answer"] = "Use Cisco AnyConnect for VPN access."
    turn1_state["source_used"] = "vectorstore"
    turn1_state["retry_count"] = 1
    turn1_state["trace"] = ["Router → KB", "Private KB retrieval → 1 chunks"]
    turn1_state["citations"] = [{"title": "vpn_policy.md"}]
    turn1_state["retrieved_contexts"] = ["Cisco AnyConnect info"]

    # Construct Turn 2
    turn2_input = WorkflowInput(
        question="How do I install it on Mac?",
        messages=[
            {"role": "user", "content": "What is the VPN policy?"},
            {"role": "assistant", "content": "Use Cisco AnyConnect for VPN access."},
            {"role": "user", "content": "How do I install it on Mac?"},
        ],
    )
    turn2_state = wf.to_initial_state(turn2_input)

    # Verify transient fields are completely scrubbed
    assert turn2_state["question"] == "How do I install it on Mac?"
    assert turn2_state["current_query"] == "How do I install it on Mac?"
    assert turn2_state["kb_docs"] == []
    assert turn2_state["web_results"] == ""
    assert turn2_state["kb_grade"] == ""
    assert turn2_state["web_grade"] == ""
    assert turn2_state["answer"] == ""
    assert turn2_state["source_used"] == ""
    assert turn2_state["retry_count"] == 0
    assert turn2_state["trace"] == []
    assert turn2_state["citations"] == []
    assert turn2_state["retrieved_contexts"] == []
    assert len(turn2_state["messages"]) == 3
    assert isinstance(turn2_state["messages"][0], HumanMessage)
    assert isinstance(turn2_state["messages"][1], AIMessage)
    assert isinstance(turn2_state["messages"][2], HumanMessage)


def test_react_agent_compiled_without_checkpointer():
    wf = ReActAgentWorkflow(llm=MockChatModel(), tools=[])
    assert wf.checkpointer is None

    compiled = wf.get_compiled_graph()
    assert getattr(compiled, "checkpointer", None) is None

    initial_state = wf.to_initial_state(
        WorkflowInput(
            question="Hello IT support",
            messages=[
                {"role": "user", "content": "Prior message"},
                {"role": "assistant", "content": "Prior response"},
                {"role": "user", "content": "Hello IT support"},
            ],
        )
    )

    result = compiled.invoke(initial_state)
    assert "messages" in result
    messages = result["messages"]
    assert len(messages) >= 3


def test_route_start_logic():
    from app.rag.engine import route_start

    # Empty messages
    assert route_start({"question": "hi", "messages": []}) == "route_question"
    # 1 message (Turn 1)
    assert route_start({"question": "hi", "messages": [HumanMessage(content="hi")]}) == "route_question"
    # Multiple messages (Turn 2+)
    assert (
        route_start({
            "question": "more",
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="hello"),
                HumanMessage(content="more"),
            ],
        })
        == "condense_query"
    )


@pytest.mark.asyncio
async def test_generation_nodes_pass_history(mock_llm, mock_kb_tool, mock_web_tool):
    """Verify that generation nodes pass history to prompts and fallback smoothly."""
    from app.rag.engine import direct_answer
    from app.services.prompts import FallbackPrompt

    custom_prompt = FallbackPrompt(
        name="rag:direct_answer",
        template="History:\n{{history}}\nQuestion: {{question}}",
    )

    state = {
        "question": "How are you?",
        "current_query": "How are you?",
        "messages": [
            HumanMessage(content="Previous inquiry"),
            AIMessage(content="Previous answer"),
            HumanMessage(content="How are you?"),
        ],
        "retry_count": 0,
    }

    with patch("app.rag.engine.get_workflow_prompt", return_value=custom_prompt):
        res = await direct_answer(state, model=mock_llm)
        assert res["source_used"] == "direct"
        # Verify the prompt compiled with history
        call_args = mock_llm.ainvoke.call_args[0][0]
        assert "Previous inquiry" in call_args
        assert "Previous answer" in call_args
        assert "How are you?" in call_args


def test_turn_2_condense_query_empty_response_fallback(mock_kb_tool, mock_web_tool):
    """Verify that if condense_query produces empty/whitespace, it falls back to current_question."""
    mock_empty_llm = MagicMock(spec=BaseChatModel)
    mock_empty_llm.ainvoke = AsyncMock(return_value=AIMessage(content="   \n  "))
    mock_empty_llm.invoke = MagicMock(return_value=AIMessage(content="Generated response"))

    classifier = FakeClassifierAdapter(route_decision="direct")
    graph = create_agentic_rag_graph(
        classifier,
        model=mock_empty_llm,
        kb_tool=mock_kb_tool,
        web_tool=mock_web_tool,
    )

    initial_state = {
        "question": "How do I install it on Mac?",
        "current_query": "How do I install it on Mac?",
        "messages": [
            HumanMessage(content="What is the VPN policy?"),
            AIMessage(content="You should use Cisco AnyConnect."),
            HumanMessage(content="How do I install it on Mac?"),
        ],
        "retry_count": 0,
    }

    result = graph.invoke(initial_state)
    trace = result.get("trace", [])
    assert any("query contextualized → 'how do i install it on mac?'" in t.lower() for t in trace)
    assert result["current_query"] == "How do I install it on Mac?"


@pytest.mark.asyncio
async def test_end_to_end_2_turn_context_and_memory_hydration(fake_retriever):
    """End-to-end 2-turn execution verifying memory hydration, condense_query, and downstream query usage."""
    from app.rag.memory.in_memory import InMemoryConversationStore
    from app.rag.tools import create_retriever_tool, create_web_search_tool
    from app.rag.workflows.registry import WorkflowRegistry
    from app.rag.workflows.runner import WorkflowRunner

    kb_tool = create_retriever_tool(retriever=fake_retriever)
    mock_search_client = MagicMock()
    mock_search_client.invoke.return_value = {"answer": "Web answer", "results": []}
    web_tool = create_web_search_tool(search_client=mock_search_client)

    captured_prompts = []

    mock_chat = MagicMock(spec=BaseChatModel)

    async def mock_ainvoke(prompt, **kwargs):
        p_str = str(prompt)
        captured_prompts.append(p_str)
        if "rephrase the follow up question" in p_str.lower() or "standalone search query" in p_str.lower():
            return AIMessage(content="How do I install Tailscale VPN on Linux?")
        return AIMessage(content="Here are the installation steps for Tailscale VPN on Linux.")

    mock_chat.ainvoke = AsyncMock(side_effect=mock_ainvoke)
    mock_chat.invoke = MagicMock(return_value=AIMessage(content="Response"))

    classifier = FakeClassifierAdapter(route_decision="kb", kb_grade="good")

    wf = SelfCorrectiveRagWorkflow(
        id="test_sc_rag",
        name="Test Self-Corrective RAG",
        classifier=classifier,
        model=mock_chat,
        kb_tool=kb_tool,
        web_tool=web_tool,
    )

    registry = WorkflowRegistry()
    registry.register(wf)
    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)

    session_id = "test-session-2-turn"

    # --- Turn 1 ---
    req1 = WorkflowInput(question="What VPN do we use?", session_id=session_id, workflow="test_sc_rag")
    resp1 = await runner.aexecute(req1)
    assert resp1.answer is not None

    # Verify Turn 1 saved
    turns = await memory.aget_turns(session_id)
    assert len(turns) == 1
    assert turns[0].user_query == "What VPN do we use?"

    # --- Turn 2 ---
    req2 = WorkflowInput(question="How do I install it on Linux?", session_id=session_id, workflow="test_sc_rag")
    resp2 = await runner.aexecute(req2)

    # Verify request.messages was assigned hydrated turns
    assert req2.messages is not None
    assert len(req2.messages) == 2  # 1 turn = HumanMessage + AIMessage
    assert req2.messages[0].content == "What VPN do we use?"

    # Verify 2 turns in memory
    turns_after = await memory.aget_turns(session_id)
    assert len(turns_after) == 2
    assert turns_after[1].user_query == "How do I install it on Linux?"

    # Verify condense_query was executed and produced contextualized query
    assert any("rephrase the follow up question" in p.lower() or "chat history:" in p.lower() for p in captured_prompts)
    # Verify generate_from_kb prompt received the chat history
    kb_gen_prompts = [p for p in captured_prompts if "private company kb" in p.lower()]
    assert len(kb_gen_prompts) > 0
    assert any("What VPN do we use?" in p for p in kb_gen_prompts)

    # Verify rewritten_query in response
    assert resp2.rewritten_query == "How do I install Tailscale VPN on Linux?"


@pytest.mark.asyncio
async def test_downstream_nodes_use_current_query(fake_retriever):
    """Verify retrieve_kb uses the contextualized query from condense_query."""
    from app.rag.tools import create_retriever_tool, create_web_search_tool

    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = [Document(page_content="doc content", metadata={"source": "doc.md"})]
    kb_tool = create_retriever_tool(retriever=mock_retriever)

    mock_search = MagicMock()
    mock_search.invoke.return_value = {"answer": "web answer", "results": []}
    web_tool = create_web_search_tool(search_client=mock_search)

    mock_classifier = MagicMock()
    mock_classifier.route.return_value = MagicMock(route="kb")
    mock_classifier.grade_kb.return_value = MagicMock(grade="good")
    mock_classifier.grade_web.return_value = MagicMock(grade="good")

    mock_llm = MagicMock(spec=BaseChatModel)
    async def mock_ainvoke(prompt, **kwargs):
        p_str = str(prompt)
        if "rephrase" in p_str.lower() or "standalone" in p_str.lower():
            return AIMessage(content="How do I install Tailscale on Ubuntu?")
        return AIMessage(content="Generated answer")

    mock_llm.ainvoke = AsyncMock(side_effect=mock_ainvoke)

    graph = create_agentic_rag_graph(
        mock_classifier,
        model=mock_llm,
        kb_tool=kb_tool,
        web_tool=web_tool,
    )

    state = {
        "question": "How do I install it?",
        "current_query": "How do I install it?",
        "messages": [
            HumanMessage(content="What VPN?"),
            AIMessage(content="Tailscale"),
            HumanMessage(content="How do I install it?"),
        ],
        "retry_count": 0,
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "kb",
        "trace": [],
        "citations": [],
        "retrieved_contexts": [],
    }

    # Test node_retrieve_kb directly through graph invocation
    res = await graph.ainvoke(state)
    # The retriever must have been called with the contextualized current_query
    mock_retriever.invoke.assert_called()
    called_query = mock_retriever.invoke.call_args[0][0]
    assert called_query == "How do I install Tailscale on Ubuntu?"


@pytest.mark.asyncio
async def test_generation_nodes_pass_chat_history_with_default_prompts(fake_retriever):
    """Verify generate_from_kb, generate_from_web, and direct_answer pass chat history even with default prompts."""
    from app.rag.engine import direct_answer
    from app.rag.tools import create_retriever_tool, create_web_search_tool

    captured = []
    mock_llm = MagicMock(spec=BaseChatModel)

    async def mock_ainvoke(prompt, **kwargs):
        captured.append(str(prompt))
        return AIMessage(content="Answer")

    mock_llm.ainvoke = AsyncMock(side_effect=mock_ainvoke)

    # 1. Test direct_answer
    state_direct = {
        "question": "Thank you!",
        "current_query": "Thank you!",
        "messages": [
            HumanMessage(content="What VPN do we use?"),
            AIMessage(content="We use Tailscale."),
            HumanMessage(content="Thank you!"),
        ],
        "retry_count": 0,
    }
    await direct_answer(state_direct, model=mock_llm)
    assert len(captured) == 1
    assert "Chat History:" in captured[0]
    assert "What VPN do we use?" in captured[0]
    assert "We use Tailscale." in captured[0]

    # 2. Test generate_from_kb via graph
    captured.clear()
    kb_tool = create_retriever_tool(retriever=fake_retriever)
    mock_search = MagicMock()
    mock_search.invoke.return_value = {"answer": "web", "results": []}
    web_tool = create_web_search_tool(search_client=mock_search)
    classifier_kb = FakeClassifierAdapter(route_decision="kb", kb_grade="good")

    graph_kb = create_agentic_rag_graph(classifier_kb, model=mock_llm, kb_tool=kb_tool, web_tool=web_tool)
    state_kb = {
        "question": "How do I configure it?",
        "current_query": "How do I configure Tailscale?",
        "messages": [
            HumanMessage(content="What VPN?"),
            AIMessage(content="Tailscale"),
            HumanMessage(content="How do I configure it?"),
        ],
        "retry_count": 0,
    }
    await graph_kb.ainvoke(state_kb)
    gen_kb_calls = [p for p in captured if "private company kb" in p.lower()]
    assert len(gen_kb_calls) >= 1
    assert "Chat History:" in gen_kb_calls[0]
    assert "What VPN?" in gen_kb_calls[0]
    assert "Tailscale" in gen_kb_calls[0]



