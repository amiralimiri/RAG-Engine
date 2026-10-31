"""Unit tests for base workflow protocols and additive API schemas."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    Attachment,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    WorkflowListResponse,
)
from app.rag.workflows.base import (
    EvalContext,
    WorkflowDefinition,
    WorkflowInfo,
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

    def get_compiled_graph(self) -> Any:
        return "mock_compiled_graph"

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        return {"question": request.question, "trace_id": trace_id}

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        return ChatResponse(
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
