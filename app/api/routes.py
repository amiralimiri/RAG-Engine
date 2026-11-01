import contextvars
import hmac
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    ForkRequest,
    RewindRequest,
    SessionDetailResponse,
    SessionListResponse,
    SessionSummaryResponse,
    WorkflowListResponse,
)
from app.core.config import get_settings
from app.rag.evaluation import get_evaluator
from app.rag.knowledge import SUPPORTED_EXTENSIONS, get_document_indexer
from app.rag.memory import get_conversation_memory
from app.rag.workflows import get_workflow_registry
from app.rag.workflows.runner import get_workflow_runner

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api")
settings = get_settings()


def capture_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Capture current contextvars snapshot and return a callable that runs inside it."""
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.run(func, *args, **kwargs)


def _submit_feedback_task(trace_id: str, score: float, name: str = "user_feedback", comment: str | None = None) -> None:
    get_evaluator().score_metric(
        trace_id=trace_id,
        metric_name=name,
        score=score,
        comment=comment,
    )


@router.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}


@router.get("/workflows")
def list_workflows():
    registry = get_workflow_registry()
    return WorkflowListResponse(
        default=ChatRequest.model_fields["workflow"].default or "fix_single_jev",
        workflows=sorted(registry.list_ids()),
        details=registry.list_info(),
    )


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, background_tasks: BackgroundTasks):
    wf_input = payload.to_workflow_input()
    wf_output = get_workflow_runner().execute(wf_input, background_tasks=background_tasks)
    return ChatResponse.from_workflow_output(wf_output)


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    wf_input = payload.to_workflow_input()
    return StreamingResponse(
        get_workflow_runner().stream(wf_input),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/sessions", response_model=SessionListResponse)
async def list_chat_sessions() -> SessionListResponse:
    memory = get_conversation_memory()
    sessions = await memory.alist_sessions(limit=50)
    return SessionListResponse(
        sessions=[SessionSummaryResponse.from_session_info(s) for s in sessions],
        total=len(sessions),
    )


@router.get("/chat/{session_id}", response_model=SessionDetailResponse)
async def get_chat_session(session_id: str) -> SessionDetailResponse:
    memory = get_conversation_memory()
    session = await memory.aget_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    turns = await memory.aget_turns(session_id)
    return SessionDetailResponse.from_domain(session, turns)


@router.delete("/chat/{session_id}")
async def delete_chat_session(session_id: str) -> dict[str, str]:
    memory = get_conversation_memory()
    await memory.aclear(session_id)
    return {"status": "deleted", "session_id": session_id}


@router.post("/chat/{session_id}/rewind")
async def rewind_chat_session(session_id: str, payload: RewindRequest) -> dict[str, Any]:
    memory = get_conversation_memory()
    session = await memory.aget_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await memory.atruncate_from(session_id, payload.turn_index)
    return {"status": "rewound", "session_id": session_id, "turn_index": payload.turn_index}


@router.post("/chat/{session_id}/fork", response_model=SessionDetailResponse)
async def fork_chat_session(session_id: str, payload: ForkRequest) -> SessionDetailResponse:
    memory = get_conversation_memory()
    session = await memory.aget_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        new_session_id = await memory.afork_session(
            session_id,
            payload.upto_turn_index,
            payload.new_session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_session = await memory.aget_session(new_session_id)
    if not new_session:
        raise HTTPException(status_code=500, detail="Failed to retrieve forked session")
    new_turns = await memory.aget_turns(new_session_id)
    return SessionDetailResponse.from_domain(new_session, new_turns)


@router.post("/feedback")
def feedback(payload: FeedbackRequest, background_tasks: BackgroundTasks):
    structlog.contextvars.bind_contextvars(trace_id=payload.trace_id)
    background_tasks.add_task(
        capture_context(_submit_feedback_task),
        trace_id=payload.trace_id,
        score=payload.score,
        name=payload.name,
        comment=payload.comment,
    )
    return {"status": "accepted", "trace_id": payload.trace_id}


@router.post("/upload")
@router.post("/ingest")
async def upload_file(file: UploadFile = File(...), x_admin_key: str = Header(default="")):
    expected = settings.admin_api_key.get_secret_value()
    if not hmac.compare_digest(x_admin_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    if safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename")
    dest = upload_dir / safe_name
    dest.write_bytes(await file.read())
    try:
        indexer = get_document_indexer()
        result = await indexer.aindex_file(dest)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc
    if not result.success:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {result.error}")
    return {
        "message": "Document indexed",
        "file": dest.name,
        "chunks": result.chunk_count,
        "ids_created": len(result.ids_created),
        "document_count": result.document_count,
        "ids": result.ids_created,
        "result": result.model_dump(),
    }


ingest = upload_file
