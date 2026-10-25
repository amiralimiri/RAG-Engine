import logging
import hmac
from pathlib import Path
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, UploadFile, File, HTTPException, Header, BackgroundTasks
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.core.telemetry import get_langfuse_callback, extract_trace_id, get_langfuse_client
from app.rag import fix_single_llm, fix_single_jev
from app.rag.vectorstore import add_documents 
from app.services.ingestion import load_file, chunk_documents, SUPPORTED
from app.services.audit import write_audit
from app.services.evaluation import score_trace_metric

logger = logging.getLogger(__name__)

WORKFLOW_ASKS = {
    "fix_single_llm": fix_single_llm.ask,
    "fix_single_jev": fix_single_jev.ask,
}


router = APIRouter(prefix="/api")
settings = get_settings()


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    workflow: str = "fix_single_jev"
    session_id: str | None = None
    user_id: str | None = None


class FeedbackRequest(BaseModel):
    trace_id: str = Field(..., min_length=1, description="Langfuse Trace ID")
    score: float = Field(..., ge=0.0, le=1.0, description="Score normalized from 0.0 to 1.0")
    name: str = Field(default="user_feedback", max_length=100)
    comment: str | None = Field(default=None, max_length=1000)


def _submit_feedback_task(
    trace_id: str,
    score: float,
    name: str = "user_feedback",
    comment: str | None = None,
) -> None:
    score_trace_metric(
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
    return {
        "default": ChatRequest.model_fields["workflow"].default,
        "workflows": sorted(WORKFLOW_ASKS),
    }


@router.post("/chat")
def chat(payload: ChatRequest):
    ask = WORKFLOW_ASKS.get(payload.workflow)
    
    if ask is None:
        raise HTTPException(status_code=400, detail=f"Unknown workflow: {payload.workflow}")

    classifier_meta = "openai_json" if payload.workflow == "fix_single_llm" else "typesafe_jev"
    handler = get_langfuse_callback(
        workflow_name=payload.workflow,
        session_id=payload.session_id,
        user_id=payload.user_id,
        metadata={"classifier": classifier_meta, "retrieval_strategy": "self_corrective"},
    )
    trace_id = extract_trace_id(handler)

    try:
        result = ask(
            payload.question,
            session_id=payload.session_id,
            user_id=payload.user_id,
            callback=handler,
        )
        trace_id = extract_trace_id(handler) or trace_id or (result.get("trace_id") if isinstance(result, dict) else None)

        write_audit(
            payload.workflow,
            payload.question,
            result["source_used"],
            result.get("trace", []),
            trace_id=trace_id,
        )

        return {
            "workflow": payload.workflow,
            "answer": result["answer"],
            "source_used": result["source_used"],
            "trace": result.get("trace", []),
            "citations": result.get("citations", []),
            "rewritten_query": result.get("current_query", payload.question),
            "trace_id": trace_id,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error in chat pipeline: %s", exc)
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc


@router.post("/feedback")
def feedback(payload: FeedbackRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        _submit_feedback_task,
        trace_id=payload.trace_id,
        score=payload.score,
        name=payload.name,
        comment=payload.comment,
    )
    return {"status": "accepted", "trace_id": payload.trace_id}



@router.post("/ingest")
async def ingest(file: UploadFile = File(...), x_admin_key: str = Header(default="")):
    # --- auth ---
    expected = settings.admin_api_key.get_secret_value()
    if not hmac.compare_digest(x_admin_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    
    # --- filename ---
    filename = file.filename
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    
    # --- extension ---
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"Supported: {', '.join(sorted(SUPPORTED))}",
        )
    
    # --- save ---
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename")
    dest = upload_dir / safe_name
    dest.write_bytes(await file.read())
    
    # --- process ---
    try:
        docs = await run_in_threadpool(load_file, dest)
        chunks = await run_in_threadpool(chunk_documents, docs)
        ids = await run_in_threadpool(add_documents, chunks)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc
    
    return {
        "message": "Document indexed",
        "file": dest.name,
        "chunks": len(chunks),
        "ids_created": len(ids),
    }