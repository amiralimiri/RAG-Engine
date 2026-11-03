"""Unit tests for HTTP SSE wire framing and SseStreamAdapter presentation seam."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.api.streaming import SseStreamAdapter, format_sse
from app.rag.workflows.base import WorkflowOutput
from app.rag.workflows.events import (
    ExecutionCompletedEvent,
    ExecutionFailedEvent,
    StepTransitionEvent,
    TokenDeltaEvent,
)


class DummyCitationModel(BaseModel):
    title: str
    url: str


class DummyPayload(BaseModel):
    message: str
    count: int


def test_format_sse_with_dict():
    """Verify format_sse formats a dictionary payload into an SSE frame."""
    payload = {"hello": "world", "num": 123}
    frame = format_sse("test_event", payload)
    assert frame == f"event: test_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def test_format_sse_with_base_model():
    """Verify format_sse formats a BaseModel payload into an SSE frame."""
    model = DummyPayload(message="structured payload", count=42)
    frame = format_sse("model_event", model)
    expected_data = json.dumps({"message": "structured payload", "count": 42}, ensure_ascii=False)
    assert frame == f"event: model_event\ndata: {expected_data}\n\n"


def test_format_sse_unicode_preservation():
    """Verify format_sse preserves UTF-8 unicode without ascii escape encoding."""
    payload = {"text": "سلام دنیا! 🚀 日本語"}
    frame = format_sse("unicode", payload)
    assert "سلام دنیا! 🚀 日本語" in frame
    assert "\\u" not in frame


def test_format_sse_default_str_fallback():
    """Verify format_sse uses default=str for non-standard serializable types."""
    from datetime import UTC, datetime

    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    payload = {"timestamp": now}
    frame = format_sse("time_event", payload)
    assert 'data: {"timestamp": "2026-11-03 12:00:00+00:00"}\n\n' in frame


@pytest.mark.asyncio
async def test_adapter_step_transition_event():
    """Verify StepTransitionEvent maps to 'step' SSE frame."""

    async def event_generator():
        yield StepTransitionEvent(step="retrieve")

    frames = [frame async for frame in SseStreamAdapter.format(event_generator())]
    assert len(frames) == 1
    assert frames[0] == 'event: step\ndata: {"step": "retrieve"}\n\n'


@pytest.mark.asyncio
async def test_adapter_token_delta_event():
    """Verify TokenDeltaEvent maps to 'token' SSE frame."""

    async def event_generator():
        yield TokenDeltaEvent(delta="Hello world")

    frames = [frame async for frame in SseStreamAdapter.format(event_generator())]
    assert len(frames) == 1
    assert frames[0] == 'event: token\ndata: {"delta": "Hello world"}\n\n'


@pytest.mark.asyncio
async def test_adapter_execution_completed_event():
    """Verify ExecutionCompletedEvent maps to 'metadata' SSE frame with canonical fields."""
    output = WorkflowOutput(
        workflow="fix_single_jev",
        answer="Paris is the capital.",
        source_used="rag",
        citations=[
            {"source": "doc1.pdf", "page": 1},
        ],
        trace=["retrieve", "generate"],
        rewritten_query="What is the capital?",
        trace_id="tr-12345",
        variant_id="var-baseline",
        session_id="ses-67890",
    )

    async def event_generator():
        yield ExecutionCompletedEvent(output=output)

    frames = [frame async for frame in SseStreamAdapter.format(event_generator())]
    assert len(frames) == 1
    frame = frames[0]
    assert frame.startswith("event: metadata\ndata: ")
    assert frame.endswith("\n\n")

    json_str = frame.split("data: ", 1)[1].rstrip("\n")
    metadata = json.loads(json_str)

    assert metadata == {
        "trace_id": "tr-12345",
        "session_id": "ses-67890",
        "workflow": "fix_single_jev",
        "source_used": "rag",
        "citations": [
            {"source": "doc1.pdf", "page": 1},
        ],
        "trace": ["retrieve", "generate"],
        "rewritten_query": "What is the capital?",
        "variant_id": "var-baseline",
    }


@pytest.mark.asyncio
async def test_adapter_execution_completed_event_with_model_citations():
    """Verify citation items that are BaseModels are serialized via model_dump."""
    output = WorkflowOutput.model_construct(
        workflow="fix_single_jev",
        answer="Paris is the capital.",
        source_used="rag",
        citations=[
            {"source": "doc1.pdf", "page": 1},
            DummyCitationModel(title="Doc 2", url="https://example.com/2"),
        ],
        trace=["retrieve"],
        rewritten_query=None,
        trace_id="tr-123",
        variant_id=None,
        session_id=None,
    )

    async def event_generator():
        yield ExecutionCompletedEvent(output=output)

    frames = [frame async for frame in SseStreamAdapter.format(event_generator())]
    assert len(frames) == 1
    json_str = frames[0].split("data: ", 1)[1].rstrip("\n")
    metadata = json.loads(json_str)
    assert metadata["citations"] == [
        {"source": "doc1.pdf", "page": 1},
        {"title": "Doc 2", "url": "https://example.com/2"},
    ]


@pytest.mark.asyncio
async def test_adapter_execution_completed_event_with_none_citations():
    """Verify execution completed event safely handles output.citations=None."""
    output = WorkflowOutput.model_construct(
        workflow="fix_single_jev",
        answer="Paris is the capital.",
        source_used="rag",
        citations=None,
        trace=["retrieve"],
        rewritten_query=None,
        trace_id="tr-123",
        variant_id=None,
        session_id=None,
    )

    async def event_generator():
        yield ExecutionCompletedEvent(output=output)

    frames = [frame async for frame in SseStreamAdapter.format(event_generator())]
    assert len(frames) == 1
    json_str = frames[0].split("data: ", 1)[1].rstrip("\n")
    metadata = json.loads(json_str)
    assert metadata["citations"] == []


@pytest.mark.asyncio
async def test_adapter_execution_failed_event():
    """Verify ExecutionFailedEvent maps to 'error' SSE frame with both error and message fields."""

    async def event_generator():
        yield ExecutionFailedEvent(error="Upstream LLM timeout", code="LLM_TIMEOUT")

    frames = [frame async for frame in SseStreamAdapter.format(event_generator())]
    assert len(frames) == 1
    expected = json.dumps(
        {
            "error": "Upstream LLM timeout",
            "message": "Upstream LLM timeout",
            "code": "LLM_TIMEOUT",
        },
        ensure_ascii=False,
    )
    assert frames[0] == f"event: error\ndata: {expected}\n\n"


@pytest.mark.asyncio
async def test_adapter_full_lifecycle_sequence():
    """Verify end-to-end event sequence formatting preservation."""
    output = WorkflowOutput(
        workflow="react_agent",
        answer="All done.",
        source_used="direct",
        trace_id="tr-seq-1",
    )

    async def event_generator():
        yield StepTransitionEvent(step="start")
        yield TokenDeltaEvent(delta="All ")
        yield TokenDeltaEvent(delta="done.")
        yield StepTransitionEvent(step="finish")
        yield ExecutionCompletedEvent(output=output)

    frames = [frame async for frame in SseStreamAdapter.format(event_generator())]
    assert len(frames) == 5
    assert frames[0] == 'event: step\ndata: {"step": "start"}\n\n'
    assert frames[1] == 'event: token\ndata: {"delta": "All "}\n\n'
    assert frames[2] == 'event: token\ndata: {"delta": "done."}\n\n'
    assert frames[3] == 'event: step\ndata: {"step": "finish"}\n\n'
    assert frames[4].startswith("event: metadata\ndata: ")


@pytest.mark.asyncio
async def test_adapter_step_transition_with_step_labels_enrichment():
    """Verify SseStreamAdapter enriches StepTransitionEvent with label and timestamp when step_labels is provided."""

    async def event_generator():
        yield StepTransitionEvent(step="retrieve_kb")

    labels = {"retrieve_kb": "Retrieving documents..."}
    frames = [frame async for frame in SseStreamAdapter.format(event_generator(), step_labels=labels)]
    assert len(frames) == 1
    frame = frames[0]
    assert frame.startswith("event: step\ndata: ")
    data = json.loads(frame.split("data: ", 1)[1].rstrip("\n"))
    assert data["step"] == "retrieve_kb"
    assert data["label"] == "Retrieving documents..."
    assert isinstance(data["timestamp"], (int, float))


def test_app_api_package_exports():
    """Verify SseStreamAdapter and format_sse are exported from app.api."""
    import app.api as api

    assert hasattr(api, "SseStreamAdapter")
    assert hasattr(api, "format_sse")
