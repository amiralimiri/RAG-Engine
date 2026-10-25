import hmac
from pathlib import Path
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, UploadFile, File, HTTPException, Header
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.rag import fix_single_llm, fix_single_jev
from app.rag.vectorstore import add_documents 
from app.services.ingestion import load_file, chunk_documents, SUPPORTED
from app.services.audit import write_audit

WORKFLOW_ASKS = {
    "fix_single_llm": fix_single_llm.ask,
    "fix_single_jev": fix_single_jev.ask,
}


router = APIRouter(prefix="/api")
settings = get_settings()


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    workflow: str = "fix_single_jev"


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

    try:
        result = ask(payload.question)
        write_audit(payload.workflow, payload.question, result["source_used"], result.get("trace", []))

        return {
            "workflow": payload.workflow,
            "answer": result["answer"],
            "source_used": result["source_used"],
            "trace": result.get("trace", []),
            "citations": result.get("citations", []),
            "rewritten_query": result.get("current_query", payload.question),
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



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