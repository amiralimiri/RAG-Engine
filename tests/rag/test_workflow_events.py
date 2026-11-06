"""Unit tests for strongly-typed workflow domain event subsystem and schemas."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.rag.workflows import (
    ExecutionCompletedEvent,
    ExecutionFailedEvent,
    StepTransitionEvent,
    TokenDeltaEvent,
    WorkflowExecutionEvent,
    WorkflowOutput,
)


class TestStepTransitionEvent:
    """Tests for StepTransitionEvent schema and serialization."""

    def test_instantiation_defaults(self) -> None:
        before = datetime.now(UTC).timestamp()
        event = StepTransitionEvent(step="retrieve")
        after = datetime.now(UTC).timestamp()

        assert event.type == "step"
        assert event.step == "retrieve"
        assert event.label == "retrieve"
        assert isinstance(event.timestamp, float)
        assert before <= event.timestamp <= after

    def test_explicit_label(self) -> None:
        event = StepTransitionEvent(step="retrieve", label="Searching internal KB...")
        assert event.type == "step"
        assert event.step == "retrieve"
        assert event.label == "Searching internal KB..."

    def test_empty_or_whitespace_label_falls_back_to_step(self) -> None:
        event_empty = StepTransitionEvent(step="retrieve", label="")
        assert event_empty.label == "retrieve"

        event_whitespace = StepTransitionEvent(step="retrieve", label="   ")
        assert event_whitespace.label == "retrieve"

    def test_explicit_type(self) -> None:
        event = StepTransitionEvent(type="step", step="grade_evidence")
        assert event.type == "step"
        assert event.step == "grade_evidence"
        assert event.label == "grade_evidence"

    def test_serialization(self) -> None:
        event = StepTransitionEvent(step="generate_answer")
        dumped = event.model_dump()
        assert dumped["type"] == "step"
        assert dumped["step"] == "generate_answer"
        assert dumped["label"] == "generate_answer"
        assert isinstance(dumped["timestamp"], float)

        json_dumped = json.loads(event.model_dump_json())
        assert json_dumped["type"] == "step"
        assert json_dumped["step"] == "generate_answer"
        assert json_dumped["label"] == "generate_answer"
        assert isinstance(json_dumped["timestamp"], float)

    def test_missing_step_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StepTransitionEvent.model_validate({})
        assert any(err["loc"] == ("step",) for err in exc_info.value.errors())

    def test_invalid_type_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StepTransitionEvent(type="not_step", step="retrieve")  # type: ignore[arg-type]
        assert any(err["loc"] == ("type",) for err in exc_info.value.errors())


class TestTokenDeltaEvent:
    """Tests for TokenDeltaEvent schema and serialization."""

    def test_instantiation_defaults(self) -> None:
        event = TokenDeltaEvent(delta="Hello ")
        assert event.type == "token"
        assert event.delta == "Hello "

    def test_explicit_type(self) -> None:
        event = TokenDeltaEvent(type="token", delta="world!")
        assert event.type == "token"
        assert event.delta == "world!"

    def test_serialization(self) -> None:
        event = TokenDeltaEvent(delta="chunk")
        dumped = event.model_dump()
        assert dumped == {"type": "token", "delta": "chunk"}

        json_dumped = json.loads(event.model_dump_json())
        assert json_dumped == {"type": "token", "delta": "chunk"}

    def test_missing_delta_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            TokenDeltaEvent.model_validate({})
        assert any(err["loc"] == ("delta",) for err in exc_info.value.errors())

    def test_invalid_type_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            TokenDeltaEvent(type="step", delta="abc")  # type: ignore[arg-type]
        assert any(err["loc"] == ("type",) for err in exc_info.value.errors())


class TestExecutionCompletedEvent:
    """Tests for ExecutionCompletedEvent schema and serialization."""

    @pytest.fixture
    def sample_output(self) -> WorkflowOutput:
        return WorkflowOutput(
            workflow="fix_single_llm",
            answer="Here is the verified answer.",
            source_used="private_kb",
            citations=[{"title": "doc_1", "text": "Evidence snippet"}],
            trace=["retrieve", "grade_evidence", "generate"],
            rewritten_query="rewritten user query",
            trace_id="trace-12345",
            variant_id="profile-fast",
            session_id="session-abc",
        )

    def test_instantiation_defaults(self, sample_output: WorkflowOutput) -> None:
        event = ExecutionCompletedEvent(output=sample_output)
        assert event.type == "completed"
        assert event.output == sample_output
        assert event.output.workflow == "fix_single_llm"
        assert event.output.answer == "Here is the verified answer."

    def test_serialization(self, sample_output: WorkflowOutput) -> None:
        event = ExecutionCompletedEvent(output=sample_output)
        dumped = event.model_dump()
        assert dumped["type"] == "completed"
        assert dumped["output"]["workflow"] == "fix_single_llm"
        assert dumped["output"]["source_used"] == "private_kb"
        assert dumped["output"]["trace_id"] == "trace-12345"
        assert len(dumped["output"]["citations"]) == 1

        json_dumped = json.loads(event.model_dump_json())
        assert json_dumped["type"] == "completed"
        assert json_dumped["output"]["answer"] == "Here is the verified answer."

    def test_dict_initialization(self) -> None:
        event = ExecutionCompletedEvent(
            output={  # type: ignore[arg-type]
                "workflow": "react_agent",
                "answer": "Answer text",
                "source_used": "direct",
            }
        )
        assert isinstance(event.output, WorkflowOutput)
        assert event.output.workflow == "react_agent"

    def test_missing_output_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ExecutionCompletedEvent.model_validate({})
        assert any(err["loc"] == ("output",) for err in exc_info.value.errors())

    def test_invalid_type_raises_validation_error(self, sample_output: WorkflowOutput) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ExecutionCompletedEvent(type="failed", output=sample_output)  # type: ignore[arg-type]
        assert any(err["loc"] == ("type",) for err in exc_info.value.errors())


class TestExecutionFailedEvent:
    """Tests for ExecutionFailedEvent schema and serialization."""

    def test_instantiation_defaults(self) -> None:
        event = ExecutionFailedEvent(error="Connection refused")
        assert event.type == "failed"
        assert event.error == "Connection refused"
        assert event.code == "EXECUTION_ERROR"

    def test_custom_code(self) -> None:
        event = ExecutionFailedEvent(error="Rate limit exceeded", code="RATE_LIMIT")
        assert event.type == "failed"
        assert event.error == "Rate limit exceeded"
        assert event.code == "RATE_LIMIT"

    def test_numeric_string_code(self) -> None:
        event = ExecutionFailedEvent(error="Internal server error", code="500")
        assert event.code == "500"

    def test_serialization(self) -> None:
        event = ExecutionFailedEvent(error="Graph loop limit reached", code="LOOP_LIMIT")
        dumped = event.model_dump()
        assert dumped == {
            "type": "failed",
            "error": "Graph loop limit reached",
            "code": "LOOP_LIMIT",
        }

        json_dumped = json.loads(event.model_dump_json())
        assert json_dumped["code"] == "LOOP_LIMIT"

    def test_missing_error_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ExecutionFailedEvent.model_validate({})
        assert any(err["loc"] == ("error",) for err in exc_info.value.errors())

    def test_invalid_type_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ExecutionFailedEvent(type="completed", error="Oops")  # type: ignore[arg-type]
        assert any(err["loc"] == ("type",) for err in exc_info.value.errors())


class TestWorkflowExecutionEventDiscriminatedUnion:
    """Tests for discriminated type matching and union validation."""

    @pytest.fixture
    def adapter(self) -> TypeAdapter[WorkflowExecutionEvent]:
        return TypeAdapter(WorkflowExecutionEvent)

    def test_discriminated_matching_step(self, adapter: TypeAdapter[WorkflowExecutionEvent]) -> None:
        before = datetime.now(UTC).timestamp()
        raw = {"type": "step", "step": "grade_evidence"}
        event = adapter.validate_python(raw)
        after = datetime.now(UTC).timestamp()
        assert isinstance(event, StepTransitionEvent)
        assert event.step == "grade_evidence"
        assert event.label == "grade_evidence"
        assert isinstance(event.timestamp, float)
        assert before <= event.timestamp <= after
        assert event.type == "step"

    def test_discriminated_matching_token(self, adapter: TypeAdapter[WorkflowExecutionEvent]) -> None:
        raw = {"type": "token", "delta": "streaming token"}
        event = adapter.validate_python(raw)
        assert isinstance(event, TokenDeltaEvent)
        assert event.delta == "streaming token"
        assert event.type == "token"

    def test_discriminated_matching_completed(self, adapter: TypeAdapter[WorkflowExecutionEvent]) -> None:
        raw = {
            "type": "completed",
            "output": {
                "workflow": "fix_single_jev",
                "answer": "Completed answer",
                "source_used": "web",
            },
        }
        event = adapter.validate_python(raw)
        assert isinstance(event, ExecutionCompletedEvent)
        assert event.output.workflow == "fix_single_jev"
        assert event.output.answer == "Completed answer"
        assert event.type == "completed"

    def test_discriminated_matching_failed_with_default_code(
        self, adapter: TypeAdapter[WorkflowExecutionEvent]
    ) -> None:
        raw = {"type": "failed", "error": "Something went wrong"}
        event = adapter.validate_python(raw)
        assert isinstance(event, ExecutionFailedEvent)
        assert event.error == "Something went wrong"
        assert event.code == "EXECUTION_ERROR"
        assert event.type == "failed"

    def test_discriminated_matching_failed_with_custom_code(self, adapter: TypeAdapter[WorkflowExecutionEvent]) -> None:
        raw = {"type": "failed", "error": "Quota exceeded", "code": "QUOTA_EXCEEDED"}
        event = adapter.validate_python(raw)
        assert isinstance(event, ExecutionFailedEvent)
        assert event.error == "Quota exceeded"
        assert event.code == "QUOTA_EXCEEDED"

    def test_invalid_type_tag_raises_validation_error(self, adapter: TypeAdapter[WorkflowExecutionEvent]) -> None:
        with pytest.raises(ValidationError) as exc_info:
            adapter.validate_python({"type": "unknown_event_type", "data": 123})
        assert any(err["type"] == "union_tag_invalid" for err in exc_info.value.errors())

    def test_missing_required_fields_for_tag(self, adapter: TypeAdapter[WorkflowExecutionEvent]) -> None:
        with pytest.raises(ValidationError):
            adapter.validate_python({"type": "step"})

        with pytest.raises(ValidationError):
            adapter.validate_python({"type": "token"})

        with pytest.raises(ValidationError):
            adapter.validate_python({"type": "completed"})

        with pytest.raises(ValidationError):
            adapter.validate_python({"type": "failed"})

    def test_json_roundtrip(self, adapter: TypeAdapter[WorkflowExecutionEvent]) -> None:
        events = [
            StepTransitionEvent(step="retrieve"),
            TokenDeltaEvent(delta="First chunk"),
            ExecutionCompletedEvent(output=WorkflowOutput(workflow="test", answer="Done", source_used="direct")),
            ExecutionFailedEvent(error="Fatal", code="500"),
        ]

        for original in events:
            json_bytes = adapter.dump_json(original)
            reconstructed = adapter.validate_json(json_bytes)
            assert reconstructed == original
            assert type(reconstructed) is type(original)

    def test_usage_in_container_model(self) -> None:
        class EventEnvelope(BaseModel):
            event: WorkflowExecutionEvent

        envelope = EventEnvelope.model_validate({"event": {"type": "step", "step": "route"}})
        assert isinstance(envelope.event, StepTransitionEvent)
        assert envelope.event.step == "route"
        assert envelope.event.label == "route"
        assert isinstance(envelope.event.timestamp, float)

        envelope_dump = envelope.model_dump()
        assert envelope_dump["event"]["type"] == "step"
        assert envelope_dump["event"]["step"] == "route"
        assert envelope_dump["event"]["label"] == "route"
        assert isinstance(envelope_dump["event"]["timestamp"], float)
