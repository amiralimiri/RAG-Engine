"""WorkflowRunner deep execution and streaming pipeline module."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
import json
import sys
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
        StreamErrorEvent,
        StreamMetadataEvent,
        StreamStepEvent,
        StreamTokenEvent,
    )
import app.core.telemetry as _core_telemetry
from app.core.telemetry import (
    extract_trace_id,
    flush_telemetry,
    get_langfuse_callback,
)
from app.rag.workflows.base import WorkflowDefinition
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry
import app.services.evaluation as _core_eval
from app.services.evaluation import run_online_evaluation_task

logger = structlog.get_logger(__name__)


def _get_langfuse_callback(**kwargs: Any) -> Any:
    routes = sys.modules.get("app.api.routes")
    if routes and hasattr(routes, "get_langfuse_callback") and routes.get_langfuse_callback is not _core_telemetry.get_langfuse_callback:
        return routes.get_langfuse_callback(**kwargs)
    return get_langfuse_callback(**kwargs)


def _extract_trace_id(handler: Any) -> str | None:
    routes = sys.modules.get("app.api.routes")
    if routes and hasattr(routes, "extract_trace_id") and routes.extract_trace_id is not _core_telemetry.extract_trace_id:
        return routes.extract_trace_id(handler)
    return extract_trace_id(handler)


def _flush_telemetry() -> None:
    routes = sys.modules.get("app.api.routes")
    if routes and hasattr(routes, "flush_telemetry") and routes.flush_telemetry is not _core_telemetry.flush_telemetry:
        routes.flush_telemetry()
    flush_telemetry()


def _get_run_online_evaluation_task() -> Any:
    routes = sys.modules.get("app.api.routes")
    if routes and hasattr(routes, "run_online_evaluation_task") and routes.run_online_evaluation_task is not _core_eval.run_online_evaluation_task:
        return routes.run_online_evaluation_task
    return run_online_evaluation_task

DEFAULT_STEP_LABELS: dict[str, str] = {
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

DEFAULT_GENERATION_NODES: set[str] = {"generate_from_kb", "generate_from_web", "direct_answer"}

# Module-level background evaluation task set with completion callbacks to prevent premature GC
_background_tasks: set[asyncio.Task[Any]] = set()


def format_sse(event: str, data: dict[str, Any] | BaseModel) -> str:
    """Format SSE frame with event name and JSON data payload."""
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def is_tool_call_chunk(chunk: Any) -> bool:
    """Filter out token deltas when chunk contains tool call chunks or structured calls."""
    if chunk is None:
        return False
    if bool(getattr(chunk, "tool_call_chunks", None)):
        return True
    if bool(getattr(chunk, "tool_calls", None)):
        return True
    if isinstance(chunk, dict) and (bool(chunk.get("tool_call_chunks")) or bool(chunk.get("tool_calls"))):
        return True

    msg = getattr(chunk, "message", None)
    if msg is not None:
        if bool(getattr(msg, "tool_call_chunks", None)):
            return True
        if bool(getattr(msg, "tool_calls", None)):
            return True
        if isinstance(msg, dict) and (bool(msg.get("tool_call_chunks")) or bool(msg.get("tool_calls"))):
            return True
    elif isinstance(chunk, dict) and isinstance(chunk.get("message"), dict):
        msg_dict = chunk["message"]
        if bool(msg_dict.get("tool_call_chunks")) or bool(msg_dict.get("tool_calls")):
            return True
    return False


class WorkflowRunner:
    """Deep module coordinating workflow execution, token streaming, telemetry, and evaluation."""

    def __init__(self, registry: WorkflowRegistry | None = None) -> None:
        self.registry = registry if registry is not None else get_workflow_registry()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def format_sse(self, event: str, data: dict[str, Any] | BaseModel) -> str:
        """Format an event frame into SSE string protocol."""
        return format_sse(event, data)

    def is_tool_call_chunk(self, chunk: Any) -> bool:
        """Filter out token deltas when chunk contains tool call chunks or structured calls."""
        return is_tool_call_chunk(chunk)

    def _prepare_execution(
        self,
        request: ChatRequest,
        streaming: bool = False,
        callback: Any = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[WorkflowDefinition, str, Any, dict[str, Any], dict[str, Any]]:
        """Resolve workflow definition, initialize telemetry contextvars, and prepare initial state."""
        if request.workflow not in self.registry and request.workflow in ("fix_single_llm", "fix_single_jev", "react_agent"):
            from app.rag.workflows import register_default_workflows
            register_default_workflows(self.registry)

        try:
            wf_def = self.registry.get(request.workflow)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown workflow: {request.workflow}")

        sanitized_tid = request.trace_id.replace("-", "").lower() if request.trace_id else (
            uuid.uuid4().hex if streaming else None
        )
        telemetry_metadata = dict(wf_def.info.metadata) if getattr(wf_def.info, "metadata", None) else {}
        if metadata:
            telemetry_metadata.update(metadata)
        if streaming:
            telemetry_metadata["streaming"] = True

        if callback is not None:
            handler = callback
        else:
            handler = _get_langfuse_callback(
                workflow_name=request.workflow,
                session_id=request.session_id,
                user_id=request.user_id,
                trace_id=sanitized_tid,
                metadata=telemetry_metadata,
            )

        trace_id = _extract_trace_id(handler) or sanitized_tid
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

        if not trace_id and (streaming or request.trace_id):
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

        if config is not None:
            run_config = dict(config)
        else:
            run_config = {}
            if handler:
                run_config["callbacks"] = [handler]
            cfg_meta = {}
            if trace_id:
                cfg_meta["trace_id"] = trace_id
            if streaming:
                cfg_meta["streaming"] = True
            if metadata:
                cfg_meta.update(metadata)
            if cfg_meta:
                run_config["metadata"] = cfg_meta
            if kwargs.get("tags"):
                run_config["tags"] = kwargs["tags"]
            thread_id = request.session_id or uuid.uuid4().hex
            run_config["configurable"] = {"thread_id": thread_id}

        return wf_def, trace_id, handler, initial_state, run_config

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
        chat_response._raw_state = final_state
        resolved_tid = _extract_trace_id(handler) or trace_id or chat_response.trace_id

        if resolved_tid:
            structlog.contextvars.bind_contextvars(trace_id=resolved_tid)
            if not chat_response.trace_id:
                chat_response.trace_id = resolved_tid
            if isinstance(final_state, dict) and "trace_id" not in final_state and request.trace_id:
                final_state["trace_id"] = resolved_tid

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

            eval_fn = _get_run_online_evaluation_task()

            if background_tasks is not None:
                background_tasks.add_task(
                    eval_fn,
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
                        eval_fn(
                            trace_id=resolved_tid,
                            question=request.question,
                            answer=chat_response.answer,
                            source_used=source_used,
                            retrieved_contexts=retrieved_contexts,
                        )
                    )
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
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
        *,
        callback: Any = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Synchronously execute a registered workflow and return ChatResponse."""
        try:
            wf_def, trace_id, handler, initial_state, run_config = self._prepare_execution(
                request,
                callback=callback,
                metadata=metadata,
                config=config,
                **kwargs,
            )
            graph = wf_def.get_compiled_graph()

            try:
                final_state = graph.invoke(initial_state, config=run_config if run_config else None)
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
            _flush_telemetry()

    async def aexecute(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
        *,
        callback: Any = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Asynchronously execute a registered workflow and return ChatResponse."""
        try:
            wf_def, trace_id, handler, initial_state, run_config = self._prepare_execution(
                request,
                callback=callback,
                metadata=metadata,
                config=config,
                **kwargs,
            )
            graph = wf_def.get_compiled_graph()

            try:
                if hasattr(graph, "ainvoke") and callable(graph.ainvoke):
                    final_state = await graph.ainvoke(initial_state, config=run_config if run_config else None)
                else:
                    final_state = await run_in_threadpool(graph.invoke, initial_state, config=run_config if run_config else None)

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
            _flush_telemetry()

    def stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[str, None]:
        """Validate workflow and return an asynchronous generator yielding pre-formatted SSE frames."""
        try:
            self.registry.get(request.workflow)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown workflow: {request.workflow}")

        return self._stream_generator(request)

    async def _stream_generator(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[str, None]:
        """Consume LangGraph astream_events v2 and yield pre-formatted SSE event strings."""
        from app.api.schemas import (
            StreamErrorEvent,
            StreamMetadataEvent,
            StreamStepEvent,
            StreamTokenEvent,
        )

        wf_def: WorkflowDefinition | None = None
        handler: Any = None
        trace_id: str | None = None
        initial_state: dict[str, Any] = {}
        config: dict[str, Any] = {}

        accumulated_trace: list[str] = []
        final_output: dict[str, Any] = {}
        tokens_delivered = 0
        answer_chunks: list[str] = []

        try:
            wf_def, trace_id, handler, initial_state, config = self._prepare_execution(request, streaming=True)
            graph = wf_def.get_compiled_graph()
            step_labels = (wf_def.info.step_labels if wf_def and wf_def.info else None) or DEFAULT_STEP_LABELS
            generation_nodes = (wf_def.info.generation_nodes if wf_def and wf_def.info else None) or DEFAULT_GENERATION_NODES
            stream_steps = getattr(request, "stream_steps", True)

            async for event in graph.astream_events(initial_state, version="v2", config=config if config else None):
                ev_type = event.get("event")
                node_name = event.get("metadata", {}).get("langgraph_node")

                # 1. Step transitions
                if stream_steps and ev_type == "on_chain_start" and node_name in step_labels:
                    yield self.format_sse(
                        "step",
                        StreamStepEvent(
                            step=node_name,
                            label=step_labels[node_name],
                            timestamp=datetime.now(timezone.utc).timestamp(),
                        ),
                    )

                # 2. Whitelisted Token Delta Streaming (Filtered: tool calls & empty strings suppressed)
                elif ev_type == "on_chat_model_stream" and node_name in generation_nodes:
                    chunk = event.get("data", {}).get("chunk")
                    delta_content = ""
                    if chunk is not None and not self.is_tool_call_chunk(chunk):
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
                        answer_chunks.append(delta_content)
                        yield self.format_sse("token", StreamTokenEvent(delta=delta_content))

                # 3. State capture on node completion
                elif ev_type == "on_chain_end":
                    node_output = event.get("data", {}).get("output")
                    if isinstance(node_output, dict):
                        if "trace" in node_output and isinstance(node_output["trace"], list):
                            for t in node_output["trace"]:
                                if t not in accumulated_trace:
                                    accumulated_trace.append(t)
                        if "messages" in node_output and isinstance(node_output["messages"], list):
                            if "messages" not in final_output:
                                final_output["messages"] = list(initial_state.get("messages", []))
                            for m in node_output["messages"]:
                                if m not in final_output["messages"]:
                                    final_output["messages"].append(m)
                        for k, v in node_output.items():
                            if k != "messages":
                                final_output[k] = v

            # 4. Fallback for non-streaming terminal nodes (e.g. 'insufficient') that set static answer
            if tokens_delivered == 0:
                fallback_answer = final_output.get("answer")
                if not fallback_answer and wf_def is not None:
                    try:
                        candidate_resp = wf_def.to_chat_response(final_output)
                        fallback_answer = candidate_resp.answer
                    except Exception:
                        pass
                if fallback_answer:
                    static_answer = str(fallback_answer)
                    tokens_delivered += 1
                    answer_chunks.append(static_answer)
                    yield self.format_sse("token", StreamTokenEvent(delta=static_answer))

            # 5. Terminal Metadata Event
            resolved_tid = _extract_trace_id(handler) or trace_id
            if wf_def is not None:
                chat_resp = wf_def.to_chat_response(final_output, trace_id=resolved_tid)
                eval_ctx = wf_def.extract_eval_context(final_output)
                final_trace = accumulated_trace or chat_resp.trace
                final_source_used = chat_resp.source_used or (
                    getattr(eval_ctx, "source_used", None)
                    or (eval_ctx.get("source_used") if isinstance(eval_ctx, dict) else None)
                    or "direct"
                )
                final_citations = chat_resp.citations
                final_rewritten_query = chat_resp.rewritten_query or final_output.get("current_query", request.question)
                final_answer = chat_resp.answer or str(final_output.get("answer") or "".join(answer_chunks))
                final_retrieved_contexts = (
                    getattr(eval_ctx, "retrieved_contexts", None)
                    or (eval_ctx.get("retrieved_contexts") if isinstance(eval_ctx, dict) else None)
                    or []
                )
            else:
                final_trace = accumulated_trace or final_output.get("trace", [])
                final_source_used = final_output.get("source_used", "direct")
                final_citations = final_output.get("citations", [])
                final_rewritten_query = final_output.get("current_query", request.question)
                final_answer = str(final_output.get("answer") or "".join(answer_chunks))
                final_retrieved_contexts = final_output.get("retrieved_contexts") or []

            metadata_dict = {
                "trace_id": resolved_tid,
                "workflow": request.workflow,
                "source_used": final_source_used,
                "citations": final_citations,
                "trace": final_trace,
                "rewritten_query": final_rewritten_query,
            }
            try:
                yield self.format_sse("metadata", StreamMetadataEvent(**metadata_dict))
            except Exception:
                yield self.format_sse("metadata", metadata_dict)

            # Safeguard background evaluation task
            if resolved_tid:
                eval_fn = _get_run_online_evaluation_task()
                eval_task = asyncio.create_task(
                    eval_fn(
                        trace_id=resolved_tid,
                        question=request.question,
                        answer=final_answer,
                        source_used=final_source_used,
                        retrieved_contexts=final_retrieved_contexts,
                    )
                )
                _background_tasks.add(eval_task)
                eval_task.add_done_callback(_background_tasks.discard)
                self._background_tasks.add(eval_task)
                eval_task.add_done_callback(self._background_tasks.discard)

            logger.info(
                "chat_query_completed",
                workflow=request.workflow,
                question=request.question,
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
                workflow=request.workflow,
                question=request.question,
                source_used="client_disconnected",
                trace=disconnect_trace + ["Stream aborted by client disconnect"],
            )
            raise

        except Exception as exc:
            logger.exception("Error during graph execution stream: %s", exc)
            if wf_def is not None:
                try:
                    fallback_state = wf_def.handle_error(exc, final_output or initial_state)
                    logger.info(
                        "workflow_error_fallback_invoked",
                        workflow=request.workflow,
                        fallback_answer=fallback_state.get("answer"),
                    )
                except Exception as fallback_exc:
                    logger.error("Error in workflow.handle_error: %s", fallback_exc)

            err_dict = {"message": f"Inference stream failed: {str(exc)}", "code": 500}
            try:
                yield self.format_sse("error", StreamErrorEvent(**err_dict))
            except Exception:
                yield self.format_sse("error", err_dict)

        finally:
            if handler and hasattr(handler, "flush") and callable(handler.flush):
                try:
                    handler.flush()
                except Exception:
                    pass
            try:
                _flush_telemetry()
            except Exception:
                pass


_default_runner: WorkflowRunner | None = None


def get_workflow_runner(registry: WorkflowRegistry | None = None) -> WorkflowRunner:
    """Return the global default WorkflowRunner singleton or a customized instance."""
    global _default_runner
    if registry is not None:
        return WorkflowRunner(registry=registry)
    if _default_runner is None:
        _default_runner = WorkflowRunner()
    return _default_runner


__all__ = [
    "DEFAULT_GENERATION_NODES",
    "DEFAULT_STEP_LABELS",
    "WorkflowRunner",
    "_background_tasks",
    "format_sse",
    "get_workflow_runner",
    "is_tool_call_chunk",
]

