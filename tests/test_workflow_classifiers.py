import pytest
from langchain_core.documents import Document

from app.rag.classifiers import FakeClassifierAdapter, WorkflowClassifier
from app.rag.state import EvidenceGrade, RouteDecision


def test_fake_classifier_adapter_satisfies_protocol():
    adapter = FakeClassifierAdapter()
    assert isinstance(adapter, WorkflowClassifier)


def test_workflow_classifier_rejects_incomplete_objects():
    class IncompleteRouter:
        def route(self, question: str):
            return RouteDecision(route="direct")

    assert not isinstance(IncompleteRouter(), WorkflowClassifier)


def test_fake_classifier_adapter_default_responses():
    adapter = FakeClassifierAdapter()

    route_res = adapter.route("Hello world")
    assert isinstance(route_res, RouteDecision)
    assert route_res.route == "kb"
    assert len(adapter.route_calls) == 1
    assert adapter.route_calls[0]["question"] == "Hello world"
    assert adapter.route_calls[0]["config"] is None

    doc = Document(page_content="content", metadata={"source": "test"})
    grade_kb_res = adapter.grade_kb("question", "query", [doc])
    assert isinstance(grade_kb_res, EvidenceGrade)
    assert grade_kb_res.grade == "good"
    assert len(adapter.grade_kb_calls) == 1
    assert adapter.grade_kb_calls[0]["kb_docs"] == [doc]

    grade_web_res = adapter.grade_web("question", "query", "web results text")
    assert isinstance(grade_web_res, EvidenceGrade)
    assert grade_web_res.grade == "good"
    assert len(adapter.grade_web_calls) == 1
    assert adapter.grade_web_calls[0]["web_results"] == "web results text"


def test_fake_classifier_adapter_custom_values():
    adapter = FakeClassifierAdapter(
        route_decision="direct",
        kb_grade="weak",
        web_grade="weak",
    )

    route_res = adapter.route("Hello direct")
    assert route_res.route == "direct"

    grade_kb_res = adapter.grade_kb("q", "cq", [])
    assert grade_kb_res.grade == "weak"

    grade_web_res = adapter.grade_web("q", "cq", "empty")
    assert grade_web_res.grade == "weak"


def test_fake_classifier_adapter_typed_values():
    adapter = FakeClassifierAdapter(
        route_decision=RouteDecision(route="direct"),
        kb_grade=EvidenceGrade(grade="weak"),
        web_grade=EvidenceGrade(grade="weak"),
    )

    assert adapter.route("hi").route == "direct"
    assert adapter.grade_kb("q", "cq", []).grade == "weak"
    assert adapter.grade_web("q", "cq", "res").grade == "weak"


def test_fake_classifier_adapter_sequence_of_values():
    adapter = FakeClassifierAdapter(
        route_decision=["kb", "direct"],
        kb_grade=["weak", "good"],
        web_grade=["weak", "good"],
    )

    assert adapter.route("q1").route == "kb"
    assert adapter.route("q2").route == "direct"
    assert adapter.route("q3").route == "direct"  # stays at last value

    assert adapter.grade_kb("q1", "cq", []).grade == "weak"
    assert adapter.grade_kb("q2", "cq", []).grade == "good"
    assert adapter.grade_kb("q3", "cq", []).grade == "good"

    assert adapter.grade_web("q1", "cq", "").grade == "weak"
    assert adapter.grade_web("q2", "cq", "").grade == "good"
    assert adapter.grade_web("q3", "cq", "").grade == "good"


def test_fake_classifier_adapter_mutation():
    adapter = FakeClassifierAdapter()
    assert adapter.route("q").route == "kb"

    adapter.route_decision = "direct"
    assert adapter.route("q").route == "direct"

    adapter.kb_grade = "weak"
    assert adapter.grade_kb("q", "cq", []).grade == "weak"

    adapter.web_grade = "weak"
    assert adapter.grade_web("q", "cq", "").grade == "weak"
