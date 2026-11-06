"""Unit tests for base workflow protocols and additive API schemas."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    Attachment,
    ChatMessage,
    ChatRequest,
    WorkflowListResponse,
)
from app.rag.workflows.base import (
    BoundExecutionContext,
    EvalContext,
    WorkflowDefinition,
    WorkflowInfo,
    WorkflowInput,
    WorkflowOutput,
)


class DummyCompliantWorkflow:
    """A test double satisfying WorkflowDefinition."""

    def __init__(self) -> None:
        self.info = WorkflowInfo(
            id="dummy_workflow",
            name="Dummy Workflow",
            description="A workflow for testing protocols",
            is_conversational=True,
            supports_streaming=True,
            step_labels={"step_1": "Doing step 1"},
            generation_nodes={"generate"},
        )

    def get_compiled_graph(self, variant_id: str | None = None) -> Any:
        return "mock_compiled_graph"

    def to_initial_state(self, input: WorkflowInput | Any, trace_id: str | None = None) -> dict[str, Any]:
        return {"question": getattr(input, "question", ""), "trace_id": trace_id}

    def to_workflow_output(self, final_state: dict[str, Any], trace_id: str | None = None) -> WorkflowOutput:
        return WorkflowOutput(
            workflow="dummy_workflow",
            answer=final_state.get("answer", "ok"),
            source_used=final_state.get("source_used", "direct"),
            trace_id=trace_id,
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used", "direct"),
            retrieved_contexts=final_state.get("retrieved_contexts", []),
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": f"Error: {exc}",
            "source_used": "error_fallback",
        }


class IncompleteWorkflow:
    """Missing required protocol methods."""

    def __init__(self) -> None:
        self.info = WorkflowInfo(
            id="bad",
            name="Bad",
            description="Bad workflow",
        )


def test_workflow_info_schema():
    info = WorkflowInfo(
        id="test_wf",
        name="Test Workflow",
        description="Testing info schema",
    )
    assert info.id == "test_wf"
    assert info.name == "Test Workflow"
    assert info.is_conversational is False
    assert info.supports_streaming is True
    assert info.step_labels == {}
    assert info.generation_nodes == set()

    with pytest.raises(ValidationError):
        WorkflowInfo(id="missing_name_and_desc")


def test_eval_context_defaults_and_custom():
    default_eval = EvalContext()
    assert default_eval.source_used == "direct"
    assert default_eval.retrieved_contexts == []

    custom_eval = EvalContext(
        source_used="vectorstore",
        retrieved_contexts=["Context chunk 1", "Context chunk 2"],
    )
    assert custom_eval.source_used == "vectorstore"
    assert len(custom_eval.retrieved_contexts) == 2


def test_workflow_definition_runtime_checking():
    compliant = DummyCompliantWorkflow()
    assert isinstance(compliant, WorkflowDefinition)

    incomplete = IncompleteWorkflow()
    assert not isinstance(incomplete, WorkflowDefinition)


def test_chat_message_and_attachment_schemas():
    msg = ChatMessage(role="user", content="Hello world")
    assert msg.role == "user"
    assert msg.content == "Hello world"

    att = Attachment(
        filename="screenshot.png",
        content_type="image/png",
        url="https://example.com/screenshot.png",
    )
    assert att.filename == "screenshot.png"
    assert att.url == "https://example.com/screenshot.png"


def test_workflow_input_schema():
    wf_in = WorkflowInput(question="How do I setup VPN?")
    assert wf_in.question == "How do I setup VPN?"
    assert wf_in.messages is None
    assert wf_in.attachments is None
    assert wf_in.session_id is None
    assert wf_in.user_id is None
    assert wf_in.trace_id is None
    assert wf_in.variant_id is None
    assert wf_in.parameters == {}

    custom_in = WorkflowInput(
        question="Query",
        messages=[{"role": "user", "content": "hello"}],
        attachments=[{"filename": "doc.pdf", "content_type": "application/pdf"}],
        session_id="s1",
        user_id="u1",
        trace_id="t1",
        variant_id="v1",
        parameters={"temperature": 0.5},
    )
    assert custom_in.session_id == "s1"
    assert custom_in.variant_id == "v1"
    assert custom_in.parameters["temperature"] == 0.5


def test_workflow_output_schema():
    wf_out = WorkflowOutput(
        workflow="test_wf",
        answer="Hello there!",
    )
    assert wf_out.workflow == "test_wf"
    assert wf_out.answer == "Hello there!"
    assert wf_out.source_used == "direct"
    assert wf_out.citations == []
    assert wf_out.trace == []
    assert wf_out.rewritten_query is None
    assert wf_out.trace_id is None
    assert wf_out.variant_id is None


def test_chat_request_backward_compatibility():
    # Old-style single query without messages/attachments
    req = ChatRequest(question="Where is IT support?")
    assert req.question == "Where is IT support?"
    assert req.workflow == "fix_single_jev"
    assert req.messages is None
    assert req.attachments is None


def test_chat_request_additive_features():
    req = ChatRequest(
        question="Can you help with this?",
        messages=[
            ChatMessage(role="user", content="Initial greeting"),
            ChatMessage(role="assistant", content="Hello! How can I help?"),
        ],
        attachments=[
            Attachment(filename="log.txt", content_type="text/plain", url="https://example.com/log.txt"),
        ],
    )
    assert len(req.messages) == 2
    assert len(req.attachments) == 1
    assert req.attachments[0].filename == "log.txt"


def test_workflow_list_response():
    resp = WorkflowListResponse(
        default="fix_single_jev",
        workflows=["fix_single_jev", "dummy_workflow"],
        details=[
            WorkflowInfo(
                id="dummy_workflow",
                name="Dummy Workflow",
                description="A workflow for testing protocols",
            )
        ],
    )
    dumped = resp.model_dump()
    assert dumped["default"] == "fix_single_jev"
    assert len(dumped["workflows"]) == 2
    assert len(dumped["details"]) == 1
    assert dumped["details"][0]["id"] == "dummy_workflow"


@pytest.fixture
def dummy_workflow() -> DummyCompliantWorkflow:
    return DummyCompliantWorkflow()


def test_bound_execution_context_instantiation_defaults(dummy_workflow: DummyCompliantWorkflow):
    initial_state = {"question": "test question", "messages": []}
    config = {"configurable": {"thread_id": "t1"}}
    trace_id = "trace-123"

    bound = BoundExecutionContext(
        workflow=dummy_workflow,
        initial_state=initial_state,
        config=config,
        trace_id=trace_id,
    )

    assert bound.workflow is dummy_workflow
    assert bound.initial_state == initial_state
    assert bound.config == config
    assert bound.trace_id == trace_id
    assert bound.session_id is None
    assert bound.variant_id is None
    assert bound.resolved_parameters == {}
    assert bound.hydrated_messages is None


def test_bound_execution_context_full_fields(dummy_workflow: DummyCompliantWorkflow):
    from langchain_core.messages import BaseMessage, HumanMessage

    messages: list[BaseMessage] = [HumanMessage(content="hello")]
    bound = BoundExecutionContext(
        workflow=dummy_workflow,
        initial_state={"question": "hello"},
        config={"tags": ["tag1"], "callbacks": []},
        trace_id="tr-456",
        session_id="ses-1",
        variant_id="var-fast",
        resolved_parameters={"temperature": 0.2},
        hydrated_messages=messages,
    )

    assert bound.session_id == "ses-1"
    assert bound.variant_id == "var-fast"
    assert bound.resolved_parameters == {"temperature": 0.2}
    assert bound.hydrated_messages == messages


def test_bound_execution_context_frozen_immutability(dummy_workflow: DummyCompliantWorkflow):
    bound = BoundExecutionContext(
        workflow=dummy_workflow,
        initial_state={"question": "q"},
        config={},
        trace_id="tr-1",
    )

    with pytest.raises(ValidationError):
        bound.trace_id = "tr-2"  # type: ignore

    with pytest.raises(ValidationError):
        bound.initial_state = {}  # type: ignore


def test_bound_execution_context_forbid_extra(dummy_workflow: DummyCompliantWorkflow):
    with pytest.raises(ValidationError):
        BoundExecutionContext(
            workflow=dummy_workflow,
            initial_state={},
            config={},
            trace_id="tr-1",
            unexpected_field="disallowed",  # type: ignore
        )


def test_bound_execution_context_as_runnable_config_defensive_copy(dummy_workflow: DummyCompliantWorkflow):
    original_config = {"tags": ["t1"], "configurable": {"k": "v"}}
    bound = BoundExecutionContext(
        workflow=dummy_workflow,
        initial_state={},
        config=original_config,
        trace_id="tr-1",
    )

    runnable_config = bound.as_runnable_config()
    assert runnable_config == original_config

    # Ensure modifying the returned dict doesn't mutate bound.config
    runnable_config["new_key"] = "new_val"
    assert "new_key" not in bound.config


def test_bound_execution_context_canonical_exports():
    import app.rag.workflows as workflows_pkg
    import app.rag.workflows.base as base_pkg

    assert "BoundExecutionContext" in workflows_pkg.__all__
    assert "BoundExecutionContext" in base_pkg.__all__
    assert getattr(workflows_pkg, "BoundExecutionContext") is BoundExecutionContext
    assert getattr(base_pkg, "BoundExecutionContext") is BoundExecutionContext


def test_workflow_output_structured_evidence_alignment():
    """Verify WorkflowOutput correctly accepts structured citations and contexts from ToolEvidenceArtifact."""
    from langchain_core.messages import ToolMessage

    from app.rag.state import Citation
    from app.rag.tools.base import ToolEvidenceArtifact, aggregate_tool_evidence

    artifact = ToolEvidenceArtifact(
        citations=[Citation(title="Network Guide", url="https://corp.local/net", type="private_kb")],
        retrieved_contexts=["Network configuration steps."],
        source_used="vectorstore",
    )
    msg = ToolMessage(content="Network config details", artifact=artifact, tool_call_id="call_999")
    evidence = aggregate_tool_evidence([msg])

    wf_out = WorkflowOutput(
        workflow="test_wf",
        answer="Follow the internal network guide.",
        source_used=evidence.source_used,
        citations=[c.model_dump() for c in evidence.citations],
        retrieved_contexts=evidence.retrieved_contexts,
    )

    assert wf_out.source_used == "vectorstore"
    assert len(wf_out.citations) == 1
    assert wf_out.citations[0].title == "Network Guide"
    assert wf_out.citations[0].type == "private_kb"
    assert wf_out.retrieved_contexts == ["Network configuration steps."]
