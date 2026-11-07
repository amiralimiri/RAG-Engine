"""Unit tests for PromptGenerationStep and QueryReformulationStep in isolation."""

from typing import Any

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from app.rag.state import Citation
from app.rag.steps import (
    PromptGenerationStep,
    QueryReformulationStep,
    get_step_class,
)
from tests.rag.steps.fakes import FakeChatModel, FakePromptStore


@pytest.mark.asyncio
async def test_kb_generation_renders_context_history_and_preserves_evidence() -> None:
    model = FakeChatModel(response_text="Connect with the company OpenVPN profile.")
    store = FakePromptStore()
    step = PromptGenerationStep(model, mode="kb", prompt_store=store)
    citations = [Citation(title="vpn.md", type="private_kb")]
    docs = [Document(page_content="Use the company OpenVPN profile.", metadata={"source": "docs/vpn.md"})]
    config: RunnableConfig = {"tags": ["generation"]}
    state: dict[str, Any] = {
        "question": "How do I connect to VPN?",
        "current_query": "vpn setup",
        "messages": [HumanMessage(content="hello"), HumanMessage(content="How do I connect to VPN?")],
        "kb_docs": docs,
        "citations": citations,
        "retrieved_contexts": ["Use the company OpenVPN profile."],
    }

    result = await step(state, config)

    assert result == {
        "answer": "Connect with the company OpenVPN profile.",
        "source_used": "vectorstore",
        "retrieved_contexts": ["Use the company OpenVPN profile."],
        "citations": citations,
        "trace": ["Answer generation → PRIVATE KB"],
    }

    render_call = store.render_calls[0]
    assert render_call["name"] == "rag:generate_kb"
    assert render_call["variables"]["context"] == "[Source: docs/vpn.md]\nUse the company OpenVPN profile."
    assert render_call["variables"]["chat_history"] == "Human: hello"
    assert render_call["variables"]["history"] == "Human: hello"

    invoke_call = model.ainvoke_calls[0]
    prompt = invoke_call["input"]
    assert prompt.startswith("Chat History:\nHuman: hello\n\n")
    assert "How do I connect to VPN?" in prompt
    assert "Use the company OpenVPN profile." in prompt
    assert invoke_call["config"]["metadata"]["prompt_name"] == "rag:generate_kb"


@pytest.mark.asyncio
async def test_web_generation_uses_web_evidence_and_web_source() -> None:
    model = FakeChatModel(response_text="Download Docker Desktop from docker.com.")
    store = FakePromptStore()
    step = PromptGenerationStep(model, mode="web", prompt_store=store)
    state: dict[str, Any] = {
        "question": "Where is the Docker installer?",
        "web_results": "Docker Desktop download page",
        "retrieved_contexts": ["web context"],
        "citations": [Citation(title="Docker", url="https://docker.com", type="web")],
    }

    result = await step(state)

    assert result["answer"] == "Download Docker Desktop from docker.com."
    assert result["source_used"] == "web_search"
    assert result["trace"] == ["Answer generation → WEB SEARCH"]
    assert result["retrieved_contexts"] == ["web context"]
    assert store.render_calls[0]["name"] == "rag:generate_web"
    assert "Docker Desktop download page" in model.ainvoke_calls[0]["input"]


@pytest.mark.asyncio
async def test_direct_generation_clears_retrieval_evidence() -> None:
    model = FakeChatModel(response_text="Hello! How can I help?")
    store = FakePromptStore()
    step = PromptGenerationStep(model, mode="direct", prompt_store=store)
    state: dict[str, Any] = {
        "question": "hi",
        "retrieved_contexts": ["should be cleared"],
        "citations": [Citation(title="should be cleared")],
    }

    result = await step(state)

    assert result == {
        "answer": "Hello! How can I help?",
        "source_used": "direct",
        "retrieved_contexts": [],
        "citations": [],
        "trace": ["Direct response → no retrieval"],
    }
    assert store.render_calls[0]["name"] == "rag:direct_answer"


@pytest.mark.asyncio
async def test_generation_does_not_duplicate_history_when_template_renders_it() -> None:
    model = FakeChatModel(response_text="answer")
    store = FakePromptStore(templates={"custom:generate": "History: {{chat_history}}\nQuestion: {{question}}"})
    step = PromptGenerationStep(model, mode="kb", prompt_store=store, prompt_key="custom:generate")
    state: dict[str, Any] = {
        "question": "new question",
        "messages": [HumanMessage(content="old turn"), HumanMessage(content="new question")],
    }

    await step(state)

    prompt = model.ainvoke_calls[0]["input"]
    assert prompt.startswith("History: Human: old turn")
    assert not prompt.startswith("Chat History:")


@pytest.mark.asyncio
async def test_generation_honors_nested_input_and_output_mappings() -> None:
    model = FakeChatModel(response_text="mapped answer")
    store = FakePromptStore()
    step = PromptGenerationStep(
        model,
        mode="kb",
        prompt_store=store,
        inputs={
            "question": "context.agent.question",
            "current_query": "context.agent.query",
            "messages": "context.agent.messages",
            "kb_docs": "context.kb.documents",
            "citations": "context.kb.citations",
            "retrieved_contexts": "context.kb.chunks",
        },
        outputs={
            "answer": "context.final.answer",
            "source_used": "context.final.source",
            "retrieved_contexts": "context.final.contexts",
            "citations": "context.final.citations",
            "trace": "context.final.trace",
        },
    )
    state: dict[str, Any] = {
        "context": {
            "agent": {
                "question": "nested question",
                "query": "nested query",
                "messages": [HumanMessage(content="old"), HumanMessage(content="nested question")],
            },
            "kb": {
                "documents": [Document(page_content="nested evidence", metadata={"source": "nested.md"})],
                "citations": [Citation(title="nested.md")],
                "chunks": ["nested evidence"],
            },
            "final": {"prior": "kept"},
        }
    }

    result = await step(state)

    assert result["context"]["final"]["answer"] == "mapped answer"
    assert result["context"]["final"]["source"] == "vectorstore"
    assert result["context"]["final"]["contexts"] == ["nested evidence"]
    assert result["context"]["final"]["citations"] == [Citation(title="nested.md")]
    assert result["context"]["final"]["prior"] == "kept"
    assert "nested evidence" in model.ainvoke_calls[0]["input"]


@pytest.mark.asyncio
async def test_condense_query_builds_standalone_query_from_history() -> None:
    model = FakeChatModel(response_text="How do I connect to the company VPN?")
    store = FakePromptStore()
    step = QueryReformulationStep(model, mode="condense", prompt_store=store)
    config: RunnableConfig = {"tags": ["condense"]}
    state: dict[str, Any] = {
        "question": "How do I connect?",
        "messages": [HumanMessage(content="I need VPN access"), HumanMessage(content="How do I connect?")],
    }

    result = await step(state, config)

    assert result == {
        "current_query": "How do I connect to the company VPN?",
        "trace": ["Query contextualized → 'How do I connect to the company VPN?'"],
    }
    render_call = store.render_calls[0]
    assert render_call["name"] == "rag:condense_query"
    assert render_call["variables"]["question"] == "How do I connect?"
    assert render_call["variables"]["chat_history"] == "Human: I need VPN access"
    assert render_call["variables"]["history"] == "Human: I need VPN access"
    assert model.ainvoke_calls[0]["config"]["metadata"]["prompt_name"] == "rag:condense_query"


@pytest.mark.asyncio
async def test_condense_query_falls_back_to_question_on_empty_model_output() -> None:
    model = FakeChatModel(response_text="   ")
    step = QueryReformulationStep(model, mode="condense", prompt_store=FakePromptStore())

    result = await step({"question": "How do I reset my password?"})

    assert result["current_query"] == "How do I reset my password?"


@pytest.mark.asyncio
async def test_rewrite_query_increments_retry_count() -> None:
    model = FakeChatModel(response_text="  company vpn setup guide  ")
    store = FakePromptStore()
    step = QueryReformulationStep(model, mode="rewrite", prompt_store=store)
    state: dict[str, Any] = {
        "question": "How do I connect to VPN?",
        "current_query": "vpn",
        "retry_count": 1,
    }

    result = await step(state)

    assert result == {
        "current_query": "company vpn setup guide",
        "retry_count": 2,
        "trace": ["Query rewrite → company vpn setup guide"],
    }
    render_call = store.render_calls[0]
    assert render_call["name"] == "rag:rewrite_query"
    assert render_call["variables"] == {"question": "How do I connect to VPN?", "current_query": "vpn"}


@pytest.mark.asyncio
async def test_rewrite_query_defaults_retry_count_to_one() -> None:
    model = FakeChatModel(response_text="better query")
    step = QueryReformulationStep(model, mode="rewrite", prompt_store=FakePromptStore())

    result = await step({"question": "question"})

    assert result["retry_count"] == 1


@pytest.mark.asyncio
async def test_reformulation_honors_nested_input_and_output_mappings() -> None:
    model = FakeChatModel(response_text="standalone")
    step = QueryReformulationStep(
        model,
        mode="condense",
        prompt_store=FakePromptStore(),
        inputs={"question": "context.agent.question", "messages": "context.agent.messages"},
        outputs={"current_query": "context.agent.condensed", "trace": "context.agent.trace"},
    )
    state: dict[str, Any] = {
        "context": {
            "agent": {
                "question": "follow up",
                "messages": [HumanMessage(content="old"), HumanMessage(content="follow up")],
                "prior": "kept",
            }
        }
    }

    result = await step(state)

    assert result["context"]["agent"]["condensed"] == "standalone"
    assert result["context"]["agent"]["prior"] == "kept"


def test_generation_and_reformulation_steps_reject_unknown_modes() -> None:
    model = FakeChatModel()

    with pytest.raises(ValueError, match="Unsupported generation mode"):
        PromptGenerationStep(model, mode="hybrid")
    with pytest.raises(ValueError, match="Unsupported reformulation mode"):
        QueryReformulationStep(model, mode="summarize")


def test_generation_steps_are_registered_under_canonical_aliases() -> None:
    assert get_step_class("prompt_generate") is PromptGenerationStep
    assert get_step_class("query_reformulate") is QueryReformulationStep


def test_chat_history_formatter_supports_dicts_and_assistant_messages() -> None:
    from app.rag.steps import format_chat_history

    history = format_chat_history(
        [
            {"role": "user", "content": "hello"},
            AIMessage(content="hi there"),
        ]
    )

    assert history == "User: hello\nAssistant: hi there"
