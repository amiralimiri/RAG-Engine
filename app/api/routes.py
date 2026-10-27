import asyncio
import contextvars
from datetime import datetime, timezone
import json
import logging
import hmac
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, UploadFile, File, HTTPException, Header, BackgroundTasks
from fastapi.responses import StreamingResponse
import structlog
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    StreamErrorEvent,
    StreamMetadataEvent,
    StreamStepEvent,
    StreamTokenEvent,
)
from app.core.config import get_settings
from app.core.telemetry import (
    get_langfuse_callback,
    extract_trace_id,
    get_langfuse_client,
    flush_telemetry,
)
from app.rag import fix_single_llm, fix_single_jev
from app.rag.vectorstore import add_documents 
from app.services.ingestion import load_file, chunk_documents, SUPPORTED
from app.services.evaluation import score_trace_metric

logger = structlog.get_logger(__name__)

STEP_LABELS: dict[str, str] = {
    "route_question": "Analyzing inquiry & routing...",
    "retrieve_kb": "Searching internal knowledge base...",
    "grade_kb": "Evaluating evidence sufficiency...",
    "search_web": "Consulting external technical documentation...",
    "grade_web": "Verifying web source credibility...",
    "rewrite_query": "Refining search terms...",
    "generate_from_kb": "Synthesizing answer from knowledge base...",
    "generate_from_web": "Drafting answer from external documentation...",
    "direct_answer": "Formulating direct response...",
    "insufficient": "Assessing fallback options...",
}

GENERATION_NODES: set[str] = {"generate_from_kb", "generate_from_web", "direct_answer"}

WORKFLOW_ASKS = {
    "fix_single_llm": fix_single_llm.ask,
    "fix_single_jev": fix_single_jev.ask,
}

WORKFLOW_GRAPHS = {
    "fix_single_llm": fix_single_llm.agent_graph,
    "fix_single_jev": fix_single_jev.agent_graph,
}


router = APIRouter(prefix="/api")
settings = get_settings()





def capture_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Capture current contextvars snapshot and return a callable that runs inside it."""
    ctx = contextvars.copy_context()

    def _context_bound_runner(*args: Any, **kwargs: Any) -> Any:
        return ctx.run(func, *args, **kwargs)

    return _context_bound_runner


def run_with_context(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute a callable inside a snapshot of current contextvars."""
    ctx = contextvars.copy_context()
    return ctx.run(func, *args, **kwargs)


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
    sanitized_tid = payload.trace_id.replace("-", "").lower() if payload.trace_id else None
    handler = get_langfuse_callback(
        workflow_name=payload.workflow,
        session_id=payload.session_id,
        user_id=payload.user_id,
        trace_id=sanitized_tid,
        metadata={"classifier": classifier_meta, "retrieval_strategy": "self_corrective"},
    )
    trace_id = extract_trace_id(handler)
    if handler is not None and not trace_id:
        ctx_tid = structlog.contextvars.get_contextvars().get("trace_id")
        trace_id = ctx_tid.replace("-", "").lower() if ctx_tid else uuid.uuid4().hex
        if hasattr(handler, "_trace_context") and isinstance(handler._trace_context, dict):
            handler._trace_context["trace_id"] = trace_id
        elif not hasattr(handler, "last_trace_id") or handler.last_trace_id is None:
            try:
                handler.last_trace_id = trace_id
            except Exception:
                pass

    context_bindings: dict[str, Any] = {"workflow": payload.workflow}
    if trace_id:
        context_bindings["trace_id"] = trace_id
    if payload.session_id:
        context_bindings["session_id"] = payload.session_id
    if payload.user_id:
        context_bindings["user_id"] = payload.user_id
    structlog.contextvars.bind_contextvars(**context_bindings)

    try:
        result = ask(
            payload.question,
            session_id=payload.session_id,
            user_id=payload.user_id,
            metadata={"trace_id": trace_id} if trace_id else None,
            callback=handler,
        )
        trace_id = extract_trace_id(handler) or trace_id or (result.get("trace_id") if isinstance(result, dict) else None)
        if trace_id:
            structlog.contextvars.bind_contextvars(trace_id=trace_id)

        logger.info(
            "chat_query_completed",
            workflow=payload.workflow,
            question=payload.question,
            source_used=result["source_used"],
            trace=result.get("trace", []),
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


def format_sse(event: str, data: dict[str, Any] | BaseModel) -> str:
    """Format SSE frame with event name and JSON data payload."""
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def chat_event_generator(
    graph: Any,
    initial_state: dict[str, Any],
    config: dict[str, Any],
    workflow: str,
    question: str,
    trace_id: str | None,
    stream_steps: bool = True,
    callback: Any = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Consume LangGraph astream_events v2, yielding typed SSE events."""
    context_bindings: dict[str, Any] = {"workflow": workflow}
    if trace_id:
        context_bindings["trace_id"] = trace_id
    if session_id:
        context_bindings["session_id"] = session_id
    if user_id:
        context_bindings["user_id"] = user_id
    structlog.contextvars.bind_contextvars(**context_bindings)

    accumulated_trace: list[str] = []
    final_output: dict[str, Any] = {}
    tokens_delivered = 0

    try:
        async for event in graph.astream_events(initial_state, version="v2", config=config):
            ev_type = event.get("event")
            node_name = event.get("metadata", {}).get("langgraph_node")

            # 1. Step transitions
            if stream_steps and ev_type == "on_chain_start" and node_name in STEP_LABELS:
                yield format_sse(
                    "step",
                    StreamStepEvent(
                        step=node_name,
                        label=STEP_LABELS[node_name],
                        timestamp=datetime.now(timezone.utc).timestamp(),
                    ),
                )

            # 2. Whitelisted Token Delta Streaming (Strictly isolated from routers and graders)
            elif ev_type == "on_chat_model_stream" and node_name in GENERATION_NODES:
                chunk = event.get("data", {}).get("chunk")
                delta_content = ""
                if chunk is not None:
                    if isinstance(chunk, dict):
                        content = chunk.get("content", "")
                    else:
                        content = getattr(chunk, "content", "")
                    if isinstance(content, str):
                        delta_content = content
                    elif isinstance(content, list):
                        delta_content = "".join(
                            c.get("text", "") if isinstance(c, dict) else (getattr(c, "text", "") or str(c))
                            for c in content
                        )
                    else:
                        delta_content = str(content) if content else ""
                if delta_content:
                    tokens_delivered += 1
                    yield format_sse("token", StreamTokenEvent(delta=delta_content))

            # 3. State capture on node completion
            elif ev_type == "on_chain_end":
                node_output = event.get("data", {}).get("output")
                if isinstance(node_output, dict):
                    if "trace" in node_output and isinstance(node_output["trace"], list):
                        for t in node_output["trace"]:
                            if t not in accumulated_trace:
                                accumulated_trace.append(t)
                    final_output.update(node_output)

        # Fallback for non-streaming terminal nodes (e.g. 'insufficient') that set static answer
        if tokens_delivered == 0 and final_output.get("answer"):
            static_answer = str(final_output["answer"])
            if static_answer:
                tokens_delivered += 1
                yield format_sse("token", StreamTokenEvent(delta=static_answer))

        # 4. Terminal Metadata Event
        resolved_tid = extract_trace_id(callback) or trace_id
        final_trace = accumulated_trace or final_output.get("trace", [])
        final_source_used = final_output.get("source_used", "direct")
        final_citations = final_output.get("citations", [])
        final_rewritten_query = final_output.get("current_query", question)

        metadata_dict = {
            "trace_id": resolved_tid,
            "workflow": workflow,
            "source_used": final_source_used,
            "citations": final_citations,
            "trace": final_trace,
            "rewritten_query": final_rewritten_query,
        }
        try:
            yield format_sse("metadata", StreamMetadataEvent(**metadata_dict))
        except Exception:
            yield format_sse("metadata", metadata_dict)

        # Finalize successful query structured log
        logger.info(
            "chat_query_completed",
            workflow=workflow,
            question=question,
            source_used=final_source_used,
            trace=final_trace,
            trace_id=resolved_tid,
        )

    except (asyncio.CancelledError, GeneratorExit):
        disconnect_trace = (
            accumulated_trace
            or (final_output.get("trace") if isinstance(final_output.get("trace"), list) else [])
        )
        logger.warning(
            "Streaming cancelled by client disconnect",
            trace_id=trace_id,
            workflow=workflow,
            question=question,
            source_used="client_disconnected",
            trace=disconnect_trace + ["Stream aborted by client disconnect"],
        )
        raise

    except Exception as exc:
        logger.exception("Error during graph execution stream: %s", exc)
        err_dict = {"message": f"Inference stream failed: {str(exc)}", "code": 500}
        try:
            yield format_sse("error", StreamErrorEvent(**err_dict))
        except Exception:
            yield format_sse("error", err_dict)

    finally:
        if callback and hasattr(callback, "flush") and callable(callback.flush):
            try:
                callback.flush()
            except Exception:
                pass
        try:
            flush_telemetry()
        except Exception:
            pass


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    graph = WORKFLOW_GRAPHS.get(payload.workflow)
    if graph is None:
        raise HTTPException(status_code=400, detail=f"Unknown workflow: {payload.workflow}")

    sanitized_tid = payload.trace_id.replace("-", "").lower() if payload.trace_id else uuid.uuid4().hex
    classifier_meta = "openai_json" if payload.workflow == "fix_single_llm" else "typesafe_jev"
    handler = get_langfuse_callback(
        workflow_name=payload.workflow,
        session_id=payload.session_id,
        user_id=payload.user_id,
        trace_id=sanitized_tid,
        metadata={"classifier": classifier_meta, "streaming": True},
    )
    trace_id = extract_trace_id(handler) or sanitized_tid

    context_bindings: dict[str, Any] = {
        "workflow": payload.workflow,
        "trace_id": trace_id,
    }
    if payload.session_id:
        context_bindings["session_id"] = payload.session_id
    if payload.user_id:
        context_bindings["user_id"] = payload.user_id
    structlog.contextvars.bind_contextvars(**context_bindings)

    initial_state = {
        "question": payload.question,
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "trace_id": trace_id,
    }

    config: dict[str, Any] = {}
    if handler:
        config["callbacks"] = [handler]
    config["metadata"] = {"trace_id": trace_id, "streaming": True}

    return StreamingResponse(
        chat_event_generator(
            graph=graph,
            initial_state=initial_state,
            config=config,
            workflow=payload.workflow,
            question=payload.question,
            trace_id=trace_id,
            stream_steps=payload.stream_steps,
            callback=handler,
            session_id=payload.session_id,
            user_id=payload.user_id,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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