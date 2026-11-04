"""Server-Sent Events (SSE) wire protocol helpers and presentation adapters."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from pydantic import BaseModel

from app.rag.workflows.events import (
    ExecutionCompletedEvent,
    ExecutionFailedEvent,
    StepTransitionEvent,
    TokenDeltaEvent,
    WorkflowExecutionEvent,
)


def format_sse(event: str, data: dict[str, Any] | BaseModel) -> str:
    """Format an SSE frame with event name and JSON-encoded data payload."""
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _serialize_citation(citation: Any) -> Any:
    """Safely serialize citation elements whether dicts or Pydantic models."""
    if isinstance(citation, BaseModel):
        return citation.model_dump(mode="json")
    if hasattr(citation, "model_dump") and callable(getattr(citation, "model_dump")):
        return getattr(citation, "model_dump")(mode="json")
    return citation


class SseStreamAdapter:
    """Presentation adapter translating domain WorkflowExecutionEvent streams into HTTP SSE text frames."""

    @classmethod
    async def format(
        cls,
        events: AsyncIterable[WorkflowExecutionEvent],
    ) -> AsyncGenerator[str, None]:  # noqa: UP043
        """Convert asynchronous workflow domain events into standard SSE text frames."""
        async for event in events:
            if isinstance(event, StepTransitionEvent):
                yield format_sse(
                    "step",
                    {
                        "step": event.step,
                        "label": event.label,
                        "timestamp": event.timestamp,
                    },
                )
            elif isinstance(event, TokenDeltaEvent):
                yield format_sse("token", {"delta": event.delta})
            elif isinstance(event, ExecutionCompletedEvent):
                output = event.output
                metadata_payload = {
                    "trace_id": output.trace_id,
                    "session_id": output.session_id,
                    "workflow": output.workflow,
                    "source_used": output.source_used,
                    "citations": [_serialize_citation(c) for c in (output.citations or [])],
                    "trace": output.trace,
                    "rewritten_query": output.rewritten_query,
                    "variant_id": output.variant_id,
                }
                yield format_sse("metadata", metadata_payload)
            elif isinstance(event, ExecutionFailedEvent):
                wire_code = 500 if event.code == "EXECUTION_ERROR" else event.code
                yield format_sse(
                    "error",
                    {
                        "error": event.error,
                        "message": event.error,
                        "code": wire_code,
                    },
                )


__all__ = ["SseStreamAdapter", "format_sse"]
