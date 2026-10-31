from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from app.rag.classifiers import (
    FakeClassifierAdapter,
    JevClassifierAdapter,
    OpenAIClassifierAdapter,
    WorkflowClassifier,
    _estimate_jev_tokens,
)
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


# ==============================================================================
# OpenAIClassifierAdapter Tests
# ==============================================================================

def test_openai_classifier_adapter_satisfies_protocol():
    adapter = OpenAIClassifierAdapter(chat_model=MagicMock())
    assert isinstance(adapter, WorkflowClassifier)


def test_openai_classifier_adapter_route():
    mock_chat_model = MagicMock()
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = RouteDecision(route="kb")
    mock_chat_model.with_structured_output.return_value = mock_structured

    adapter = OpenAIClassifierAdapter(chat_model=mock_chat_model)
    config = {"metadata": {"trace_id": "test-trace"}}
    decision = adapter.route("How do I connect to VPN?", config=config)

    assert isinstance(decision, RouteDecision)
    assert decision.route == "kb"
    mock_chat_model.with_structured_output.assert_called_once_with(
        RouteDecision, method="json_mode"
    )

    call_args = mock_structured.invoke.call_args
    prompt_used = call_args[0][0]
    run_cfg = call_args[1]["config"]
    assert "How do I connect to VPN?" in prompt_used
    assert "metadata" in run_cfg
    assert run_cfg["metadata"]["trace_id"] == "test-trace"
    assert "prompt_name" in run_cfg["metadata"] or "langfuse_prompt" in run_cfg["metadata"]


def test_openai_classifier_adapter_grade_kb():
    mock_chat_model = MagicMock()
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = {"grade": "good"}
    mock_chat_model.with_structured_output.return_value = mock_structured

    adapter = OpenAIClassifierAdapter(chat_model=mock_chat_model)
    docs = [Document(page_content="VPN guide text", metadata={"source": "vpn.md"})]
    grade = adapter.grade_kb("Need VPN", "VPN setup", docs)

    assert isinstance(grade, EvidenceGrade)
    assert grade.grade == "good"
    mock_chat_model.with_structured_output.assert_called_once_with(
        EvidenceGrade, method="json_mode"
    )

    call_args = mock_structured.invoke.call_args
    prompt_used = call_args[0][0]
    assert "Need VPN" in prompt_used
    assert "VPN setup" in prompt_used
    assert "VPN guide text" in prompt_used


def test_openai_classifier_adapter_grade_web():
    mock_chat_model = MagicMock()
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = EvidenceGrade(grade="weak")
    mock_chat_model.with_structured_output.return_value = mock_structured

    adapter = OpenAIClassifierAdapter(chat_model=mock_chat_model)
    grade = adapter.grade_web("Driver issue", "download driver", "Search results text")

    assert isinstance(grade, EvidenceGrade)
    assert grade.grade == "weak"
    mock_chat_model.with_structured_output.assert_called_once_with(
        EvidenceGrade, method="json_mode"
    )

    call_args = mock_structured.invoke.call_args
    prompt_used = call_args[0][0]
    assert "Driver issue" in prompt_used
    assert "download driver" in prompt_used
    assert "Search results text" in prompt_used


# ==============================================================================
# JevClassifierAdapter Tests
# ==============================================================================

def test_jev_classifier_adapter_satisfies_protocol():
    adapter = JevClassifierAdapter(classifier=MagicMock())
    assert isinstance(adapter, WorkflowClassifier)


def test_estimate_jev_tokens():
    assert _estimate_jev_tokens("", "") == {"input": 1, "output": 1, "total": 2}
    assert _estimate_jev_tokens("hi", "no") == {"input": 1, "output": 1, "total": 2}
    assert _estimate_jev_tokens("12345678", "abcd") == {"input": 2, "output": 1, "total": 3}


def test_jev_classifier_adapter_route():
    mock_jev = MagicMock()
    mock_choice = MagicMock()
    mock_choice.choice = "direct"
    mock_choice.confidence = 0.99
    mock_result = MagicMock()
    mock_result.choices = {"route": mock_choice}
    mock_jev.invoke.return_value = mock_result

    adapter = JevClassifierAdapter(classifier=mock_jev)
    decision = adapter.route("Hello there")

    assert isinstance(decision, RouteDecision)
    assert decision.route == "direct"
    mock_jev.invoke.assert_called_once()
    payload = mock_jev.invoke.call_args[0][0]
    assert payload["state"] == "Hello there"
    assert "route" in payload["questions"]


def test_jev_classifier_adapter_grade_kb():
    mock_jev = MagicMock()
    mock_choice = MagicMock()
    mock_choice.choice = "good"
    mock_result = MagicMock()
    mock_result.choices = {"grade": mock_choice}
    mock_jev.invoke.return_value = mock_result

    adapter = JevClassifierAdapter(classifier=mock_jev)
    docs = [Document(page_content="Internal SSO troubleshooting steps", metadata={"source": "sso.md"})]
    grade = adapter.grade_kb("SSO login failed", "SSO troubleshoot", docs)

    assert isinstance(grade, EvidenceGrade)
    assert grade.grade == "good"
    mock_jev.invoke.assert_called_once()
    payload = mock_jev.invoke.call_args[0][0]
    assert "SSO login failed" in payload["state"]
    assert "SSO troubleshoot" in payload["state"]
    assert "Internal SSO troubleshooting steps" in payload["state"]


def test_jev_classifier_adapter_grade_web():
    mock_jev = MagicMock()
    mock_choice = MagicMock()
    mock_choice.choice = "weak"
    mock_result = MagicMock()
    mock_result.choices = {"grade": mock_choice}
    mock_jev.invoke.return_value = mock_result

    adapter = JevClassifierAdapter(classifier=mock_jev)
    grade = adapter.grade_web("Printer offline", "printer wifi fix", "Generic manufacturer website")

    assert isinstance(grade, EvidenceGrade)
    assert grade.grade == "weak"
    mock_jev.invoke.assert_called_once()
    payload = mock_jev.invoke.call_args[0][0]
    assert "Printer offline" in payload["state"]
    assert "printer wifi fix" in payload["state"]
    assert "Generic manufacturer website" in payload["state"]


def test_jev_classifier_adapter_records_telemetry():
    mock_jev = MagicMock()
    mock_choice = MagicMock()
    mock_choice.choice = "kb"
    mock_result = MagicMock()
    mock_result.choices = {"route": mock_choice}
    mock_jev.invoke.return_value = mock_result

    mock_langfuse = MagicMock()
    mock_obs = MagicMock()
    mock_langfuse.start_observation.return_value = mock_obs

    with patch("app.rag.classifiers.get_langfuse_client", return_value=mock_langfuse):
        adapter = JevClassifierAdapter(classifier=mock_jev)
        decision = adapter.route("Question", config={"metadata": {"trace_id": "test-trace"}})

        assert decision.route == "kb"
        mock_langfuse.start_observation.assert_called_once()
        obs_kwargs = mock_langfuse.start_observation.call_args[1]
        assert obs_kwargs["name"] == "jev_router"
        assert obs_kwargs["as_type"] == "generation"
        assert obs_kwargs["trace_context"] == {"trace_id": "test-trace"}
        assert "usage_details" in obs_kwargs
        assert obs_kwargs["usage_details"]["input"] >= 1
        assert obs_kwargs["usage_details"]["output"] >= 1
        mock_obs.end.assert_called_once()
