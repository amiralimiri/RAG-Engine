"""Unit tests for ClassifierRouteStep and ClassifierGradingStep in isolation."""

from typing import Any

import pytest
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.rag.steps import (
    ClassifierGradingStep,
    ClassifierRouteStep,
    get_step_class,
)
from tests.rag.steps.fakes import FakeWorkflowClassifier


@pytest.mark.asyncio
async def test_route_uses_question_for_first_turn() -> None:
    classifier = FakeWorkflowClassifier(route_decision="kb")
    step = ClassifierRouteStep(classifier)
    config: RunnableConfig = {"tags": ["router"]}
    state: dict[str, Any] = {
        "question": "How do I connect to VPN?",
        "current_query": "stale query",
        "messages": [HumanMessage(content="How do I connect to VPN?")],
    }

    result = await step(state, config)

    assert result == {
        "current_query": "How do I connect to VPN?",
        "source_used": "kb",
        "trace": ["Router → KB"],
    }
    assert classifier.route_calls == [{"question": "How do I connect to VPN?", "config": config}]


@pytest.mark.asyncio
async def test_route_uses_current_query_on_follow_up_turns() -> None:
    classifier = FakeWorkflowClassifier(route_decision="direct")
    step = ClassifierRouteStep(classifier)
    state: dict[str, Any] = {
        "question": "and my laptop?",
        "current_query": "How do I set up my company laptop?",
        "messages": [HumanMessage(content="hi"), HumanMessage(content="and my laptop?")],
    }

    result = await step(state)

    assert result["current_query"] == "How do I set up my company laptop?"
    assert result["source_used"] == "direct"
    assert result["trace"] == ["Router → DIRECT"]
    assert classifier.route_calls[0]["question"] == "How do I set up my company laptop?"


@pytest.mark.asyncio
async def test_route_falls_back_to_question_when_current_query_missing() -> None:
    classifier = FakeWorkflowClassifier()
    step = ClassifierRouteStep(classifier)
    state: dict[str, Any] = {
        "question": "reset password",
        "messages": [HumanMessage(content="hi"), HumanMessage(content="reset password")],
    }

    result = await step(state)

    assert result["current_query"] == "reset password"
    assert classifier.route_calls[0]["question"] == "reset password"


@pytest.mark.asyncio
async def test_route_honors_nested_input_and_output_mappings() -> None:
    classifier = FakeWorkflowClassifier(route_decision="kb")
    step = ClassifierRouteStep(
        classifier,
        inputs={
            "question": "context.agent.question",
            "current_query": "context.agent.query",
            "messages": "context.agent.messages",
        },
        outputs={
            "current_query": "context.routing.query",
            "source_used": "context.routing.route",
            "trace": "context.routing.trace",
        },
    )
    state: dict[str, Any] = {
        "context": {
            "agent": {
                "question": "follow up",
                "query": "standalone follow up",
                "messages": [{"role": "user", "content": "earlier"}, {"role": "user", "content": "follow up"}],
            },
            "routing": {"prior": "kept"},
        }
    }

    result = await step(state)

    assert result["context"]["routing"]["query"] == "standalone follow up"
    assert result["context"]["routing"]["route"] == "kb"
    assert result["context"]["routing"]["prior"] == "kept"
    assert classifier.route_calls[0]["question"] == "standalone follow up"


@pytest.mark.asyncio
async def test_grade_kb_emits_grade_and_trace() -> None:
    classifier = FakeWorkflowClassifier(kb_grade="weak")
    step = ClassifierGradingStep(classifier, mode="kb")
    config: RunnableConfig = {"tags": ["grade"]}
    docs = [Document(page_content="vpn notes", metadata={"source": "docs/vpn.md"})]
    state: dict[str, Any] = {
        "question": "How do I connect to VPN?",
        "current_query": "vpn setup",
        "kb_docs": docs,
    }

    result = await step(state, config)

    assert result == {"kb_grade": "weak", "trace": ["KB evidence grade → WEAK"]}
    assert classifier.grade_kb_calls == [
        {
            "question": "How do I connect to VPN?",
            "current_query": "vpn setup",
            "kb_docs": docs,
            "config": config,
        }
    ]


@pytest.mark.asyncio
async def test_grade_web_falls_back_to_question_for_search_query() -> None:
    classifier = FakeWorkflowClassifier(web_grade="good")
    step = ClassifierGradingStep(classifier, mode="web")
    state: dict[str, Any] = {
        "question": "Where is the Docker installer?",
        "web_results": "Docker Desktop download page",
    }

    result = await step(state)

    assert result == {"web_grade": "good", "trace": ["Web evidence grade → GOOD"]}
    assert classifier.grade_web_calls == [
        {
            "question": "Where is the Docker installer?",
            "current_query": "Where is the Docker installer?",
            "web_results": "Docker Desktop download page",
            "config": None,
        }
    ]


@pytest.mark.asyncio
async def test_grade_honors_nested_input_and_output_mappings() -> None:
    classifier = FakeWorkflowClassifier(kb_grade="good")
    step = ClassifierGradingStep(
        classifier,
        mode="kb",
        inputs={
            "question": "context.question",
            "current_query": "context.search.query",
            "kb_docs": "context.kb.documents",
        },
        outputs={"kb_grade": "context.kb.grade", "trace": "context.kb.trace"},
    )
    state: dict[str, Any] = {
        "context": {
            "question": "original",
            "search": {"query": "nested search"},
            "kb": {"documents": [Document(page_content="evidence")], "prior": "preserved"},
        }
    }

    result = await step(state)

    assert result["context"]["kb"]["grade"] == "good"
    assert result["context"]["kb"]["prior"] == "preserved"
    assert classifier.grade_kb_calls[0]["current_query"] == "nested search"


def test_grading_step_rejects_unknown_mode() -> None:
    classifier = FakeWorkflowClassifier()

    with pytest.raises(ValueError, match="Unsupported grading mode"):
        ClassifierGradingStep(classifier, mode="hybrid")


def test_classifier_steps_are_registered_under_canonical_aliases() -> None:
    assert get_step_class("classifier_route") is ClassifierRouteStep
    assert get_step_class("classifier_grade") is ClassifierGradingStep
