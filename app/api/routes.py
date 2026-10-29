import contextvars
import hmac
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
import structlog

from app.api.schemas import ChatRequest, ChatResponse, FeedbackRequest, WorkflowListResponse
from app.core.config import get_settings
from app.core.telemetry import extract_trace_id, flush_telemetry, get_langfuse_callback, get_langfuse_client
from app.rag.knowledge import SUPPORTED_EXTENSIONS, get_document_indexer
from app.rag.workflows import get_workflow_registry
from app.rag.workflows.legacy import WORKFLOW_ASKS, WORKFLOW_GRAPHS
from app.rag.workflows.runner import _background_tasks, get_workflow_runner
from app.services.evaluation import run_online_evaluation_task, score_trace_metric

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api")
settings = get_settings()


def capture_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Capture current contextvars snapshot and return a callable that runs inside it."""
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.run(func, *args, **kwargs)


def run_with_context(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute a callable inside a snapshot of current contextvars."""
    return contextvars.copy_context().run(func, *args, **kwargs)


def _submit_feedback_task(
    trace_id: str, score: float, name: str = "user_feedback", comment: str | None = None
) -> None:
    score_trace_metric(trace_id=trace_id, metric_name=name, score=score, comment=comment)

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


def _runner_hooks() -> dict[str, Any]:
    return {
        "callback_factory": get_langfuse_callback, "trace_extractor": extract_trace_id,
        "telemetry_flusher": flush_telemetry, "eval_task_fn": run_online_evaluation_task,
    }


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, background_tasks: BackgroundTasks):
    return get_workflow_runner().execute(payload, background_tasks=background_tasks, **_runner_hooks())


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    return StreamingResponse(
        get_workflow_runner().stream(payload, **_runner_hooks()),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/feedback")
def feedback(payload: FeedbackRequest, background_tasks: BackgroundTasks):
    structlog.contextvars.bind_contextvars(trace_id=payload.trace_id)
    background_tasks.add_task(
        capture_context(_submit_feedback_task),
        trace_id=payload.trace_id, score=payload.score, name=payload.name, comment=payload.comment,
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
        "message": "Document indexed", "file": dest.name, "chunks": result.chunk_count,
        "ids_created": len(result.ids_created), "document_count": result.document_count,
        "ids": result.ids_created, "result": result.model_dump(),
    }


ingest = upload_file
