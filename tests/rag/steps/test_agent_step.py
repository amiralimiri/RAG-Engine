"""Unit tests for AgentStep tool loops, scoped tooling, and dynamic handoffs."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.rag.state import Citation
from app.rag.steps import (
    DEFAULT_STEP_CLASSES,
    AgentStep,
    HandoffTransferInput,
    create_handoff_tool,
    get_step_class,
)
from app.rag.tools import FakeRAGTool, ToolEvidenceArtifact
from tests.rag.steps.fakes import FakeChatModel, FakePromptStore


def _ai_tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _kb_tool() -> FakeRAGTool:
    return FakeRAGTool(
        name="knowledge_base_search",
        content="Use the company OpenVPN profile.",
        artifact=ToolEvidenceArtifact(
            citations=[Citation(title="vpn.md", type="private_kb")],
            retrieved_contexts=["Use the company OpenVPN profile."],
            source_used="vectorstore",
        ),
    )


def test_agent_step_is_registered_in_catalog() -> None:
    assert get_step_class("agent") is AgentStep
    assert DEFAULT_STEP_CLASSES["agent"] is AgentStep


def test_create_handoff_tool_builds_typed_transfer_tool() -> None:
    tool = create_handoff_tool("security_agent")

    assert isinstance(tool, StructuredTool)
    assert tool.name == "transfer_to_security_agent"
    assert "security_agent" in tool.description
    schema = tool.args_schema
    assert isinstance(schema, type)
    assert issubclass(schema, BaseModel)
    assert schema is HandoffTransferInput
    assert "reason" in schema.model_fields
    assert tool.invoke({"reason": "requires security review"}) == (
        "Transferring control to agent 'security_agent'. Reason: requires security review"
    )


def test_create_handoff_tool_rejects_empty_target() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        create_handoff_tool("   ")


@pytest.mark.asyncio
async def test_agent_step_executes_tool_loop_and_emits_evidence() -> None:
    tool = _kb_tool()
    model = FakeChatModel(
        response_messages=[
            _ai_tool_call("knowledge_base_search", {"query": "vpn setup"}, "call_1"),
            AIMessage(content="Connect with the company OpenVPN profile."),
        ]
    )
    store = FakePromptStore()
    step = AgentStep(model, [tool], prompt_store=store)
    config: RunnableConfig = {"tags": ["agent"]}
    state: dict[str, Any] = {
        "question": "How do I connect to VPN?",
        "messages": [HumanMessage(content="How do I connect to VPN?")],
    }

    result = await step(state, config)

    assert result["answer"] == "Connect with the company OpenVPN profile."
    assert result["citations"] == [Citation(title="vpn.md", type="private_kb")]
    assert result["retrieved_contexts"] == ["Use the company OpenVPN profile."]
    assert result["source_used"] == "vectorstore"
    assert result["trace"] == ["Agent 'agent' executed tool 'knowledge_base_search'"]
    assert "active_agent" not in result

    assert tool.aexecute_calls[0]["input"] == {"query": "vpn setup"}
    assert tool.aexecute_calls[0]["config"]["metadata"]["prompt_name"] == "rag:agent"
    assert len(result["messages"]) == 3
    assert isinstance(result["messages"][1], ToolMessage)
    assert result["messages"][1].tool_call_id == "call_1"
    assert result["messages"][1].artifact == tool.artifact

    render_call = store.render_calls[0]
    assert render_call["name"] == "rag:agent"
    assert render_call["variables"]["question"] == "How do I connect to VPN?"

    first_prompt_messages = model.ainvoke_calls[0]["input"]
    assert first_prompt_messages[0].type == "system"
    assert "How do I connect to VPN?" in first_prompt_messages[0].content

    assert [bound.name for bound in model.bind_calls[0]] == ["knowledge_base_search"]


@pytest.mark.asyncio
async def test_agent_step_scopes_bound_tools_to_domain_tools_and_handoffs() -> None:
    tool = _kb_tool()
    model = FakeChatModel(response_messages=[AIMessage(content="Done.")])
    step = AgentStep(
        model,
        [tool],
        handoffs=["security_agent", "billing_agent"],
        prompt_store=FakePromptStore(),
    )

    result = await step({"question": "Who handles this?", "messages": []})

    assert [bound.name for bound in model.bind_calls[0]] == [
        "knowledge_base_search",
        "transfer_to_security_agent",
        "transfer_to_billing_agent",
    ]
    assert result["answer"] == "Done."
    assert result["trace"] == ["Agent 'agent' completed"]


@pytest.mark.asyncio
async def test_agent_step_dynamic_handoff_updates_active_agent_and_trace() -> None:
    model = FakeChatModel(
        response_messages=[
            _ai_tool_call(
                "transfer_to_security_agent",
                {"reason": "requires security review"},
                "call_handoff",
            )
        ]
    )
    step = AgentStep(
        model,
        handoffs=["security_agent"],
        prompt_store=FakePromptStore(),
    )

    result = await step(
        {
            "question": "I think my account was compromised.",
            "messages": [HumanMessage(content="I think my account was compromised.")],
        }
    )

    assert result["active_agent"] == "security_agent"
    assert result["trace"] == [
        "Agent 'agent' → transfer_to_security_agent: requires security review"
    ]
    assert len(model.ainvoke_calls) == 1
    assert "answer" not in result

    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].tool_calls[0]["name"] == "transfer_to_security_agent"
    assert isinstance(result["messages"][1], ToolMessage)
    assert result["messages"][1].tool_call_id == "call_handoff"
    assert result["messages"][1].name == "transfer_to_security_agent"
    assert "requires security review" in result["messages"][1].content


@pytest.mark.asyncio
async def test_agent_step_answers_all_parallel_tool_calls_when_handoff_fires() -> None:
    model = FakeChatModel(
        response_messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "knowledge_base_search", "args": {"query": "breach"}, "id": "call_1", "type": "tool_call"},
                    {
                        "name": "transfer_to_security_agent",
                        "args": {"reason": "needs incident response"},
                        "id": "call_2",
                        "type": "tool_call",
                    },
                ],
            )
        ]
    )
    tool = _kb_tool()
    step = AgentStep(
        model,
        [tool],
        handoffs=["security_agent"],
        prompt_store=FakePromptStore(),
    )

    result = await step({"question": "breach", "messages": []})

    assert result["active_agent"] == "security_agent"
    assert result["trace"] == [
        "Agent 'agent' executed tool 'knowledge_base_search'",
        "Agent 'agent' → transfer_to_security_agent: needs incident response",
    ]
    assert tool.aexecute_calls[0]["input"] == {"query": "breach"}
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ["call_1", "call_2"]


@pytest.mark.asyncio
async def test_agent_step_skips_parallel_tool_calls_after_handoff() -> None:
    model = FakeChatModel(
        response_messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "transfer_to_security_agent",
                        "args": {"reason": "needs incident response"},
                        "id": "call_1",
                        "type": "tool_call",
                    },
                    {"name": "knowledge_base_search", "args": {"query": "breach"}, "id": "call_2", "type": "tool_call"},
                ],
            )
        ]
    )
    tool = _kb_tool()
    step = AgentStep(
        model,
        [tool],
        handoffs=["security_agent"],
        prompt_store=FakePromptStore(),
    )

    result = await step({"question": "breach", "messages": []})

    assert result["active_agent"] == "security_agent"
    assert result["trace"] == [
        "Agent 'agent' → transfer_to_security_agent: needs incident response",
        "Agent 'agent' skipped tool 'knowledge_base_search' after handoff",
    ]
    assert tool.aexecute_calls == []
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ["call_1", "call_2"]
    assert "skipped" in tool_messages[1].content


@pytest.mark.asyncio
async def test_agent_step_rejects_tool_calls_outside_its_scope() -> None:
    model = FakeChatModel(
        response_messages=[
            _ai_tool_call("web_search", {"query": "vpn"}, "call_blocked"),
            AIMessage(content="I only have KB access."),
        ]
    )
    step = AgentStep(model, [], prompt_store=FakePromptStore())

    result = await step({"question": "search the web", "messages": []})

    assert result["answer"] == "I only have KB access."
    assert result["trace"] == ["Agent 'agent' blocked unavailable tool 'web_search'"]
    assert isinstance(result["messages"][1], ToolMessage)
    assert "not available to agent 'agent'" in result["messages"][1].content


@pytest.mark.asyncio
async def test_agent_step_isolates_tool_failures_and_continues_loop() -> None:
    async def _failing_tool(query: str) -> str:
        raise RuntimeError("provider unavailable")

    failing_tool = StructuredTool.from_function(
        coroutine=_failing_tool,
        name="failing_tool",
        description="Always fails for testing.",
    )
    model = FakeChatModel(
        response_messages=[
            _ai_tool_call("failing_tool", {"query": "vpn"}, "call_error"),
            AIMessage(content="Recovered after tool failure."),
        ]
    )
    step = AgentStep(model, [failing_tool], prompt_store=FakePromptStore())

    result = await step({"question": "vpn", "messages": []})

    assert result["answer"] == "Recovered after tool failure."
    assert result["trace"] == ["Agent 'agent' tool 'failing_tool' failed"]
    assert isinstance(result["messages"][1], ToolMessage)
    assert result["messages"][1].status == "error"
    assert "provider unavailable" in result["messages"][1].content


@pytest.mark.asyncio
async def test_agent_step_stops_after_max_iterations() -> None:
    model = FakeChatModel(
        response_messages=[
            _ai_tool_call("knowledge_base_search", {"query": "vpn"}, "call_loop_1"),
            _ai_tool_call("knowledge_base_search", {"query": "vpn"}, "call_loop_2"),
        ]
    )
    step = AgentStep(
        model,
        [_kb_tool()],
        max_iterations=2,
        prompt_store=FakePromptStore(),
    )

    result = await step({"question": "vpn", "messages": []})

    assert result["trace"] == [
        "Agent 'agent' executed tool 'knowledge_base_search'",
        "Agent 'agent' executed tool 'knowledge_base_search'",
        "Agent 'agent' stopped after 2 iterations",
    ]
    assert "answer" not in result


@pytest.mark.asyncio
async def test_agent_step_honors_nested_input_and_output_mappings() -> None:
    model = FakeChatModel(response_messages=[AIMessage(content="Mapped answer.")])
    step = AgentStep(
        model,
        [],
        prompt_store=FakePromptStore(),
        inputs={"question": "context.question"},
        outputs={"answer": "context.agent_answer", "trace": "trace"},
    )

    result = await step({"context": {"question": "nested question"}, "trace": []})

    assert result["context"]["agent_answer"] == "Mapped answer."
    assert "answer" not in result
