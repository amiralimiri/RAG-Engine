"""WorkflowRunner deep execution and streaming pipeline module."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
import uuid

from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
import structlog

if TYPE_CHECKING:
    from app.api.schemas import (
        ChatRequest,
        ChatResponse,
    )

from app.core.telemetry import (
    extract_trace_id,
    flush_telemetry,
    get_langfuse_callback,
)
from app.rag.workflows.base import WorkflowDefinition
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry
from app.services.evaluation import run_online_evaluation_task

logger = structlog.get_logger(__name__)


class WorkflowRunner:
    """Deep module coordinating workflow execution, token streaming, telemetry, and evaluation."""

    def __init__(self, registry: WorkflowRegistry | None = None) -> None:
        self.registry = registry if registry is not None else get_workflow_registry()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _prepare_execution(
        self,
        request: ChatRequest,
    ) -> tuple[WorkflowDefinition, str, Any, dict[str, Any], dict[str, Any]]:
        """Resolve workflow definition, initialize telemetry contextvars, and prepare initial state."""
        try:
            wf_def = self.registry.get(request.workflow)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown workflow: {request.workflow}")

        sanitized_tid = request.trace_id.replace("-", "").lower() if request.trace_id else None
        telemetry_metadata = dict(wf_def.info.metadata) if getattr(wf_def.info, "metadata", None) else {}

        handler = get_langfuse_callback(
            workflow_name=request.workflow,
            session_id=request.session_id,
            user_id=request.user_id,
            trace_id=sanitized_tid,
            metadata=telemetry_metadata,
        )

        trace_id = extract_trace_id(handler) or sanitized_tid
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

        if not trace_id:
            ctx_tid = structlog.contextvars.get_contextvars().get("trace_id")
            trace_id = ctx_tid.replace("-", "").lower() if ctx_tid else uuid.uuid4().hex

        context_bindings: dict[str, Any] = {"workflow": request.workflow}
        if trace_id:
            context_bindings["trace_id"] = trace_id
        if request.session_id:
            context_bindings["session_id"] = request.session_id
        if request.user_id:
            context_bindings["user_id"] = request.user_id
        structlog.contextvars.bind_contextvars(**context_bindings)

        initial_state = wf_def.to_initial_state(request, trace_id=trace_id)

        config: dict[str, Any] = {}
        if handler:
            config["callbacks"] = [handler]
        if trace_id:
            config["metadata"] = {"trace_id": trace_id}
        thread_id = request.session_id or uuid.uuid4().hex
        config["configurable"] = {"thread_id": thread_id}

        return wf_def, trace_id, handler, initial_state, config

    def _finalize_execution(
        self,
        wf_def: WorkflowDefinition,
        request: ChatRequest,
        final_state: dict[str, Any],
        trace_id: str,
        handler: Any,
        background_tasks: BackgroundTasks | None = None,
    ) -> ChatResponse:
        """Convert final state to ChatResponse, bind resolved trace context, and schedule online evaluation."""
        chat_response = wf_def.to_chat_response(final_state, trace_id=trace_id)
        resolved_tid = extract_trace_id(handler) or trace_id or chat_response.trace_id

        if resolved_tid:
            structlog.contextvars.bind_contextvars(trace_id=resolved_tid)
            if not chat_response.trace_id:
                chat_response.trace_id = resolved_tid

        logger.info(
            "chat_query_completed",
            workflow=request.workflow,
            question=request.question,
            source_used=chat_response.source_used,
            trace=chat_response.trace,
            trace_id=resolved_tid,
        )

        if resolved_tid:
            eval_ctx = wf_def.extract_eval_context(final_state)
            source_used = (
                getattr(eval_ctx, "source_used", None)
                or (eval_ctx.get("source_used") if isinstance(eval_ctx, dict) else None)
                or chat_response.source_used
                or "direct"
            )
            retrieved_contexts = (
                getattr(eval_ctx, "retrieved_contexts", None)
                or (eval_ctx.get("retrieved_contexts") if isinstance(eval_ctx, dict) else None)
                or []
            )

            if background_tasks is not None:
                background_tasks.add_task(
                    run_online_evaluation_task,
                    trace_id=resolved_tid,
                    question=request.question,
                    answer=chat_response.answer,
                    source_used=source_used,
                    retrieved_contexts=retrieved_contexts,
                )
            else:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop is not None and loop.is_running():
                    task = loop.create_task(
                        run_online_evaluation_task(
                            trace_id=resolved_tid,
                            question=request.question,
                            answer=chat_response.answer,
                            source_used=source_used,
                            retrieved_contexts=retrieved_contexts,
                        )
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                else:
                    logger.debug("No active event loop for background evaluation scheduling")

        if handler is not None and hasattr(handler, "flush") and callable(handler.flush):
            try:
                handler.flush()
            except Exception:
                pass

        return chat_response

    def execute(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
    ) -> ChatResponse:
        """Synchronously execute a registered workflow and return ChatResponse."""
        try:
            wf_def, trace_id, handler, initial_state, config = self._prepare_execution(request)
            graph = wf_def.get_compiled_graph()

            try:
                final_state = graph.invoke(initial_state, config=config if config else None)
                if not isinstance(final_state, dict):
                    final_state = {"answer": str(final_state)}
            except Exception as exc:
                logger.exception("Error executing workflow %s: %s", request.workflow, exc)
                final_state = wf_def.handle_error(exc, initial_state)

            return self._finalize_execution(
                wf_def=wf_def,
                request=request,
                final_state=final_state,
                trace_id=trace_id,
                handler=handler,
                background_tasks=background_tasks,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Error in chat pipeline: %s", exc)
            raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc
        finally:
            flush_telemetry()

    async def aexecute(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
    ) -> ChatResponse:
        """Asynchronously execute a registered workflow and return ChatResponse."""
        try:
            wf_def, trace_id, handler, initial_state, config = self._prepare_execution(request)
            graph = wf_def.get_compiled_graph()

            try:
                if hasattr(graph, "ainvoke") and callable(graph.ainvoke):
                    final_state = await graph.ainvoke(initial_state, config=config if config else None)
                else:
                    final_state = await run_in_threadpool(graph.invoke, initial_state, config=config if config else None)

                if not isinstance(final_state, dict):
                    final_state = {"answer": str(final_state)}
            except Exception as exc:
                logger.exception("Error executing workflow %s: %s", request.workflow, exc)
                final_state = wf_def.handle_error(exc, initial_state)

            return self._finalize_execution(
                wf_def=wf_def,
                request=request,
                final_state=final_state,
                trace_id=trace_id,
                handler=handler,
                background_tasks=background_tasks,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Error in chat pipeline: %s", exc)
            raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc
        finally:
            flush_telemetry()

    async def stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[str, None]:
        """Consume LangGraph astream_events v2 and yield pre-formatted SSE event strings."""
        raise NotImplementedError("stream() will be implemented in Ticket 02")


_default_runner: WorkflowRunner | None = None


def get_workflow_runner(registry: WorkflowRegistry | None = None) -> WorkflowRunner:
    """Return the global default WorkflowRunner singleton or a customized instance."""
    global _default_runner
    if registry is not None:
        return WorkflowRunner(registry=registry)
    if _default_runner is None:
        _default_runner = WorkflowRunner()
    return _default_runner
