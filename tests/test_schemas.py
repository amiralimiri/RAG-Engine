import json

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    StreamErrorEvent,
    StreamMetadataEvent,
    StreamStepEvent,
    StreamTokenEvent,
)


def test_chat_request_defaults():
    req = ChatRequest(question="What is the VPN policy?")
    assert req.question == "What is the VPN policy?"
    assert req.workflow == "fix_single_jev"
    assert req.session_id is None
    assert req.user_id is None
    assert req.trace_id is None
    assert req.stream_steps is True


def test_chat_request_custom_fields():
    req = ChatRequest(
        question="What is the VPN policy?",
        workflow="fix_single_llm",
        session_id="sess-123",
        user_id="user-456",
        trace_id="trace-789",
        stream_steps=False,
    )
    assert req.workflow == "fix_single_llm"
    assert req.session_id == "sess-123"
    assert req.user_id == "user-456"
    assert req.trace_id == "trace-789"
    assert req.stream_steps is False

    # Serialization roundtrip
    dumped = req.model_dump_json()
    parsed = ChatRequest.model_validate_json(dumped)
    assert parsed == req


def test_chat_request_validation():
    with pytest.raises(ValidationError):
        ChatRequest(question="a")  # min_length 2

    with pytest.raises(ValidationError):
        ChatRequest(question="a" * 3001)  # max_length 3000


def test_chat_response_serialization():
    resp = ChatResponse(
        workflow="fix_single_jev",
        answer="Follow these instructions...",
        source_used="private_kb",
        trace=["Router -> KB", "Answer generation"],
        citations=[{"title": "VPN Guide", "url": "", "type": "private_kb"}],
        rewritten_query="vpn policy",
        trace_id="trace-123",
    )
    dumped = resp.model_dump_json()
    data = json.loads(dumped)
    assert data["workflow"] == "fix_single_jev"
    assert data["answer"] == "Follow these instructions..."
    assert data["source_used"] == "private_kb"
    assert len(data["trace"]) == 2
    assert len(data["citations"]) == 1
    assert data["rewritten_query"] == "vpn policy"
    assert data["trace_id"] == "trace-123"

    parsed = ChatResponse.model_validate_json(dumped)
    assert parsed == resp


def test_feedback_request_validation():
    req = FeedbackRequest(trace_id="trace-123", score=0.8, name="helpfulness", comment="Great answer")
    assert req.score == 0.8
    assert req.name == "helpfulness"

    with pytest.raises(ValidationError):
        FeedbackRequest(trace_id="trace-123", score=1.5)

    with pytest.raises(ValidationError):
        FeedbackRequest(trace_id="trace-123", score=-0.1)

    dumped = req.model_dump_json()
    assert FeedbackRequest.model_validate_json(dumped) == req


def test_stream_step_event():
    event = StreamStepEvent(step="retrieve_kb", label="Searching internal knowledge base...")
    assert event.step == "retrieve_kb"
    assert event.label == "Searching internal knowledge base..."
    assert isinstance(event.timestamp, float)
    assert event.timestamp > 0

    explicit_event = StreamStepEvent(step="route_question", label="Routing...", timestamp=1700000000.5)
    assert explicit_event.timestamp == 1700000000.5

    dumped = event.model_dump_json()
    parsed = StreamStepEvent.model_validate_json(dumped)
    assert parsed.step == event.step
    assert parsed.label == event.label
    assert parsed.timestamp == event.timestamp


def test_stream_token_event():
    event = StreamTokenEvent(delta="Hello, ")
    assert event.delta == "Hello, "

    dumped = event.model_dump_json()
    parsed = StreamTokenEvent.model_validate_json(dumped)
    assert parsed.delta == "Hello, "

    with pytest.raises(ValidationError):
        StreamTokenEvent()  # delta is required


def test_stream_metadata_event():
    event = StreamMetadataEvent(
        trace_id="trace-abc-123",
        source_used="private_kb",
        workflow="fix_single_jev",
        trace=["Router -> KB", "Retrieve -> 3 chunks"],
        citations=[{"title": "doc.pdf", "url": "", "type": "private_kb"}],
        rewritten_query="search query",
    )
    assert event.trace_id == "trace-abc-123"
    assert event.source_used == "private_kb"
    assert event.workflow == "fix_single_jev"
    assert len(event.trace) == 2
    assert len(event.citations) == 1
    assert event.rewritten_query == "search query"

    dumped = event.model_dump_json()
    parsed = StreamMetadataEvent.model_validate_json(dumped)
    assert parsed == event

    # Defaults test
    minimal_event = StreamMetadataEvent(
        source_used="direct",
        workflow="fix_single_llm",
    )
    assert minimal_event.trace_id is None
    assert minimal_event.trace == []
    assert minimal_event.citations == []
    assert minimal_event.rewritten_query is None


def test_stream_error_event():
    event = StreamErrorEvent(message="Internal processing error")
    assert event.message == "Internal processing error"
    assert event.code == 500

    custom_event = StreamErrorEvent(message="Service Unavailable", code=503)
    assert custom_event.code == 503

    dumped = event.model_dump_json()
    parsed = StreamErrorEvent.model_validate_json(dumped)
    assert parsed == event
