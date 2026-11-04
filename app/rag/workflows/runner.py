"""WorkflowRunner deep execution and streaming pipeline module."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import Any, cast

import structlog
from fastapi import HTTPException
from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.concurrency import run_async_safely
from app.core.telemetry import flush_telemetry
from app.rag.evaluation import Evaluator, get_evaluator
from app.rag.memory import (
    ConversationMemoryAdapter,
    NoOpMemoryStore,
)
from app.rag.workflows.base import (
    EvalContext,
    WorkflowDefinition,
    WorkflowInput,
    WorkflowOutput,
)
from app.rag.workflows.binder import (
    BoundExecutionContext,
    ExecutionContextBinder,
)
from app.rag.workflows.events import (
    ExecutionCompletedEvent,
    ExecutionFailedEvent,
    StepTransitionEvent,
    TokenDeltaEvent,
    WorkflowExecutionEvent,
)
from app.rag.workflows.listeners import (
    ExecutionLifecycleListener,
    MemoryPersistenceListener,
    OnlineEvaluationListener,
)
from app.rag.workflows.registry import WorkflowRegistry, get_workflow_registry

logger = structlog.get_logger(__name__)

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


class ExecutionResult(BaseModel):
    """Normalized execution result bundling response and domain EvalContext."""

    response: WorkflowOutput
    eval_context: EvalContext


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

    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        *,
        binder: ExecutionContextBinder | None = None,
        evaluator: Evaluator | None = None,
        memory: ConversationMemoryAdapter | None = None,
        listeners: list[ExecutionLifecycleListener] | None = None,
        callback_factory: Callable[..., Any] | None = None,
        trace_extractor: Callable[[Any], str | None] | None = None,
        telemetry_flusher: Callable[[], None] | None = None,
        eval_task_fn: Callable[..., Any] | None = None,
        settings: Any | None = None,
    ) -> None:
        if binder is not None:
            self._binder = binder
            self.registry = registry if registry is not None else binder.registry
        else:
            self.registry = registry if registry is not None else get_workflow_registry()
            self._binder = ExecutionContextBinder(
                registry=self.registry,
                memory=memory,
                callback_factory=callback_factory,
                trace_extractor=trace_extractor,
                settings=settings,
            )

        self._evaluator = evaluator
        self._telemetry_flusher = telemetry_flusher
        self._eval_task_fn = eval_task_fn
        self._background_tasks: set[asyncio.Task[Any]] = set()

        if listeners is not None:
            self._listeners: list[ExecutionLifecycleListener] = list(listeners)
        else:
            defaults: list[ExecutionLifecycleListener] = []
            effective_memory = self._binder.memory
            if effective_memory is not None and not isinstance(effective_memory, NoOpMemoryStore):
                defaults.append(MemoryPersistenceListener(memory=effective_memory))
            if evaluator is not None or eval_task_fn is not None:
                defaults.append(
                    OnlineEvaluationListener(
                        evaluator=evaluator
                        or (get_evaluator() if evaluator is None and eval_task_fn is None else None),
                        eval_task_fn=eval_task_fn,
                    )
                )
            self._listeners = defaults

    @property
    def binder(self) -> ExecutionContextBinder:
        """The active ExecutionContextBinder instance."""
        return self._binder

    @property
    def settings(self) -> Any:
        return self._binder.settings

    @property
    def memory(self) -> ConversationMemoryAdapter | None:
        """The read-path conversation memory adapter."""
        return self._binder.memory

    @property
    def listeners(self) -> list[ExecutionLifecycleListener]:
        """Registered execution lifecycle listeners."""
        return self._listeners

    @property
    def eval_task_fn(self) -> Callable[..., Any] | None:
        """Injected evaluation task function if configured."""
        return self._eval_task_fn

    @property
    def evaluator(self) -> Evaluator:
        """Injected evaluator or canonical default."""
        return self._evaluator or get_evaluator()

    @property
    def callback_factory(self) -> Callable[..., Any]:
        """Injected Langfuse callback factory or canonical default."""
        return self._binder.callback_factory

    @property
    def trace_extractor(self) -> Callable[[Any], str | None]:
        return self._binder.trace_extractor

    @property
    def telemetry_flusher(self) -> Callable[[], None]:
        return self._telemetry_flusher or flush_telemetry

    def is_tool_call_chunk(self, chunk: Any) -> bool:
        """Filter out token deltas when chunk contains tool call chunks or structured calls."""
        return is_tool_call_chunk(chunk)

    def resolve_workflow(self, workflow_name: str) -> WorkflowDefinition:
        """Resolve workflow definition from binder, auto-registering defaults if empty."""
        return self._binder.resolve_workflow(workflow_name)

    def _resolve_workflow(self, workflow_name: str) -> WorkflowDefinition:
        """Resolve workflow definition from binder, auto-registering defaults if empty."""
        return self._binder.resolve_workflow(workflow_name)

    def _to_workflow_input(
        self,
        request: Any,
        trace_id: str | None = None,
        variant_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        messages: Sequence[BaseMessage | dict[str, Any]] | None = None,
    ) -> WorkflowInput:
        """Ensure telemetry trace_id, variant_id, parameters, and messages are bound to domain WorkflowInput."""
        return self._binder._to_workflow_input(
            request,
            trace_id=trace_id,
            variant_id=variant_id,
            parameters=parameters,
            messages=messages,
        )

    async def _adispatch_listeners(
        self,
        event_type: str,
        request: WorkflowInput,
        output: WorkflowOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        """Centralized lifecycle event dispatcher awaiting listeners with fail-open fault isolation."""
        resolved_event = event_type.lower().removeprefix("on_")

        if resolved_event not in {"completed", "error", "cancelled"}:
            logger.warning(
                "unknown_listener_event_skipped",
                event_type=event_type,
                resolved_event=resolved_event,
            )
            return

        if output is not None:
            if hasattr(output, "normalize_contexts") and callable(output.normalize_contexts):
                output.normalize_contexts()
            else:
                raw_contexts = getattr(output, "retrieved_contexts", None)
                if raw_contexts is None:
                    output.retrieved_contexts = []
                elif isinstance(raw_contexts, list):
                    output.retrieved_contexts = [str(c) for c in raw_contexts]
                elif isinstance(raw_contexts, str):
                    output.retrieved_contexts = [raw_contexts]
                else:
                    output.retrieved_contexts = [str(raw_contexts)]

        if resolved_event == "completed" and output is None:
            logger.warning("listener_dispatch_missing_output", event_type=resolved_event)
            return

        if resolved_event == "error" and error is None:
            logger.warning("listener_dispatch_missing_error", event_type=resolved_event)
            return

        for listener in self.listeners:
            try:
                if resolved_event == "completed" and output is not None:
                    await listener.on_completed(request, output)
                elif resolved_event == "error" and error is not None:
                    await listener.on_error(request, error)
                elif resolved_event == "cancelled":
                    await listener.on_cancelled(request, output)
            except Exception as exc:
                logger.warning(
                    f"listener_{resolved_event}_failed",
                    listener=listener.__class__.__name__,
                    error=str(exc),
                )

        if resolved_event == "completed" and output is not None:
            for listener in self.listeners:
                coro: Any = None
                try:
                    coro = listener.on_background_task(request, output)
                    task = asyncio.create_task(coro)
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

                    def _on_task_done(t: asyncio.Task[Any], listener_name: str = listener.__class__.__name__) -> None:
                        if not t.cancelled():
                            exc = t.exception()
                            if exc is not None:
                                logger.warning(
                                    "listener_background_task_failed",
                                    listener=listener_name,
                                    error=str(exc),
                                )

                    task.add_done_callback(_on_task_done)
                except Exception as exc:
                    if coro is not None and inspect.iscoroutine(coro):
                        coro.close()
                    logger.warning(
                        "listener_background_task_scheduling_failed",
                        listener=listener.__class__.__name__,
                        error=str(exc),
                    )

    def _dispatch_listeners(
        self,
        event_type: str,
        request: WorkflowInput,
        output: WorkflowOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        """Synchronously dispatch lifecycle events via persistent background event loop."""
        run_async_safely(self._adispatch_listeners, event_type, request, output, error)

    def _finalize_execution(
        self,
        wf_def: WorkflowDefinition,
        request: WorkflowInput,
        final_state: dict[str, Any],
        trace_id: str | None,
        handler: Any,
        dispatch_listeners: bool = True,
    ) -> WorkflowOutput:
        """Convert final state to domain WorkflowOutput, bind resolved trace context, and schedule online evaluation."""
        tr_extractor = self.trace_extractor

        domain_output = wf_def.to_workflow_output(final_state, trace_id=trace_id)
        if getattr(request, "session_id", None) and not domain_output.session_id:
            domain_output.session_id = request.session_id
        resolved_tid = tr_extractor(handler) or trace_id or domain_output.trace_id

        if resolved_tid:
            structlog.contextvars.bind_contextvars(trace_id=resolved_tid)
            if not domain_output.trace_id:
                domain_output.trace_id = resolved_tid
            if isinstance(final_state, dict) and "trace_id" not in final_state and request.trace_id:
                final_state["trace_id"] = resolved_tid

        if not domain_output.variant_id:
            domain_output.variant_id = (
                final_state.get("variant_id") if isinstance(final_state, dict) else None
            ) or getattr(request, "variant_id", None)

        logger.info(
            "chat_query_completed",
            workflow=getattr(request, "workflow", None) or wf_def.info.id,
            question=request.question,
            source_used=domain_output.source_used,
            trace=domain_output.trace,
            trace_id=resolved_tid,
            variant_id=domain_output.variant_id,
        )

        eval_ctx = wf_def.extract_eval_context(final_state)
        retrieved_contexts = (
            getattr(eval_ctx, "retrieved_contexts", None)
            or (eval_ctx.get("retrieved_contexts") if isinstance(eval_ctx, dict) else None)
            or []
        )
        domain_output.retrieved_contexts = retrieved_contexts

        if handler is not None and hasattr(handler, "flush") and callable(handler.flush):
            try:
                handler.flush()
            except Exception:
                pass

        if dispatch_listeners:
            self._dispatch_listeners(
                "completed",
                request,
                domain_output,
            )

        return domain_output

    async def _afinalize_execution(
        self,
        wf_def: WorkflowDefinition,
        request: WorkflowInput,
        final_state: dict[str, Any],
        trace_id: str | None,
        handler: Any,
    ) -> WorkflowOutput:
        """Asynchronously finalize execution and persist turn via direct coroutine await."""
        domain_output = self._finalize_execution(
            wf_def=wf_def,
            request=request,
            final_state=final_state,
            trace_id=trace_id,
            handler=handler,
            dispatch_listeners=False,
        )
        await self._adispatch_listeners(
            "completed",
            request,
            domain_output,
        )
        return domain_output

    def _execute_with_state(
        self,
        request: WorkflowInput,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        bound: BoundExecutionContext | None = None,
        **kwargs: Any,
    ) -> tuple[WorkflowOutput, dict[str, Any]]:
        """Synchronously execute workflow and return (WorkflowOutput, final_state) without model mutation."""
        error_dispatched = False
        try:
            if bound is None:
                bound = self._binder.bind(
                    request,
                    callback=callback,
                    metadata=metadata,
                    config=config,
                    **kwargs,
                )
            wf_def = bound.workflow
            trace_id = bound.trace_id
            initial_state = bound.initial_state
            run_config = bound.as_runnable_config()
            callbacks = run_config.get("callbacks") or []
            handler = callbacks[0] if callbacks else None

            variant_id = (
                bound.variant_id
                or initial_state.get("variant_id")
                or run_config.get("configurable", {}).get("variant_id")
            )
            graph = (
                wf_def.get_compiled_graph(variant_id=variant_id)
                if variant_id is not None
                else wf_def.get_compiled_graph()
            )

            try:
                final_state = graph.invoke(initial_state, config=run_config if run_config else None)
                if not isinstance(final_state, dict):
                    final_state = {"answer": str(final_state)}
            except Exception as exc:
                self._dispatch_listeners("error", request, error=exc)
                error_dispatched = True
                logger.exception("Error executing workflow %s: %s", getattr(request, "workflow", "unknown"), exc)
                final_state = wf_def.handle_error(exc, initial_state)

            eff_trace_id = (
                trace_id
                if (handler is not None or getattr(request, "trace_id", None) is not None or kwargs.get("streaming"))
                else None
            )
            response = self._finalize_execution(
                wf_def=wf_def,
                request=request,
                final_state=final_state,
                trace_id=eff_trace_id,
                handler=handler,
            )
            return response, final_state
        except HTTPException as exc:
            if not error_dispatched:
                self._dispatch_listeners("error", request, error=exc)
            raise
        except Exception as exc:
            if not error_dispatched:
                self._dispatch_listeners("error", request, error=exc)
            logger.exception("Error in chat pipeline: %s", exc)
            raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc
        finally:
            try:
                self.telemetry_flusher()
            except Exception:
                pass

    def execute(
        self,
        request: WorkflowInput,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> WorkflowOutput:
        """Synchronously execute a registered workflow and return WorkflowOutput."""
        response, _ = self._execute_with_state(
            request,
            callback=callback,
            metadata=metadata,
            config=config,
            **kwargs,
        )
        return response

    def execute_with_context(
        self,
        request: WorkflowInput,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Public execution entrypoint returning both WorkflowOutput and normalized EvalContext."""
        bound = self._binder.bind(
            request,
            callback=callback,
            metadata=metadata,
            config=config,
            **kwargs,
        )
        response, final_state = self._execute_with_state(
            request,
            callback=callback,
            metadata=metadata,
            config=config,
            bound=bound,
            **kwargs,
        )
        eval_context = bound.workflow.extract_eval_context(final_state)
        return ExecutionResult(response=response, eval_context=eval_context)

    async def _aexecute_with_state(
        self,
        request: WorkflowInput,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        bound: BoundExecutionContext | None = None,
        **kwargs: Any,
    ) -> tuple[WorkflowOutput, dict[str, Any]]:
        """Asynchronously execute workflow and return (WorkflowOutput, final_state) without model mutation."""
        error_dispatched = False
        try:
            if bound is None:
                bound = await self._binder.abind(
                    request,
                    callback=callback,
                    metadata=metadata,
                    config=config,
                    **kwargs,
                )
            wf_def = bound.workflow
            trace_id = bound.trace_id
            initial_state = bound.initial_state
            run_config = bound.as_runnable_config()
            callbacks = run_config.get("callbacks") or []
            handler = callbacks[0] if callbacks else None

            variant_id = (
                bound.variant_id
                or initial_state.get("variant_id")
                or run_config.get("configurable", {}).get("variant_id")
            )
            graph = (
                wf_def.get_compiled_graph(variant_id=variant_id)
                if variant_id is not None
                else wf_def.get_compiled_graph()
            )

            try:
                if hasattr(graph, "ainvoke") and callable(graph.ainvoke):
                    final_state = await cast(
                        Awaitable[Any], graph.ainvoke(initial_state, config=run_config if run_config else None)
                    )
                else:
                    final_state = await run_in_threadpool(
                        graph.invoke, initial_state, config=run_config if run_config else None
                    )

                if not isinstance(final_state, dict):
                    final_state = {"answer": str(final_state)}
            except Exception as exc:
                await self._adispatch_listeners("error", request, error=exc)
                error_dispatched = True
                logger.exception("Error executing workflow %s: %s", getattr(request, "workflow", "unknown"), exc)
                final_state = wf_def.handle_error(exc, initial_state)

            eff_trace_id = (
                trace_id
                if (handler is not None or getattr(request, "trace_id", None) is not None or kwargs.get("streaming"))
                else None
            )
            response = await self._afinalize_execution(
                wf_def=wf_def,
                request=request,
                final_state=final_state,
                trace_id=eff_trace_id,
                handler=handler,
            )
            return response, final_state
        except HTTPException as exc:
            if not error_dispatched:
                await self._adispatch_listeners("error", request, error=exc)
            raise
        except Exception as exc:
            if not error_dispatched:
                await self._adispatch_listeners("error", request, error=exc)
            logger.exception("Error in chat pipeline: %s", exc)
            raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc
        finally:
            try:
                self.telemetry_flusher()
            except Exception:
                pass

    async def aexecute(
        self,
        request: WorkflowInput,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> WorkflowOutput:
        """Asynchronously execute a registered workflow and return WorkflowOutput."""
        response, _ = await self._aexecute_with_state(
            request,
            callback=callback,
            metadata=metadata,
            config=config,
            **kwargs,
        )
        return response

    def stream(
        self,
        request: WorkflowInput,
    ) -> AsyncGenerator[WorkflowExecutionEvent, None]:  # noqa: UP043
        """Validate workflow and return an asynchronous generator yielding typed WorkflowExecutionEvent instances."""
        wf_name = (request.workflow if hasattr(request, "workflow") and request.workflow else None) or "fix_single_jev"
        try:
            self._binder.resolve_workflow(wf_name)
        except Exception as exc:
            self._dispatch_listeners("error", request, error=exc)
            raise
        return self._stream_generator(request)

    async def _stream_generator(
        self,
        request: WorkflowInput,
    ) -> AsyncGenerator[WorkflowExecutionEvent, None]:  # noqa: UP043
        """Consume LangGraph astream_events v2 and yield typed domain events."""
        tr_extractor = self.trace_extractor
        tel_flusher = self.telemetry_flusher

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
            bound = await self._binder.abind(
                request,
                streaming=True,
            )
            wf_def = bound.workflow
            trace_id = bound.trace_id
            config = bound.as_runnable_config()
            initial_state = bound.initial_state
            callbacks = config.get("callbacks") or []
            handler = callbacks[0] if callbacks else None

            variant_id = (
                bound.variant_id or initial_state.get("variant_id") or config.get("configurable", {}).get("variant_id")
            )
            graph = (
                wf_def.get_compiled_graph(variant_id=variant_id)
                if variant_id is not None
                else wf_def.get_compiled_graph()
            )
            step_labels = (wf_def.info.step_labels if wf_def and wf_def.info else None) or DEFAULT_STEP_LABELS
            generation_nodes = (
                wf_def.info.generation_nodes if wf_def and wf_def.info else None
            ) or DEFAULT_GENERATION_NODES
            stream_steps = getattr(request, "stream_steps", True)

            async for event in graph.astream_events(initial_state, version="v2", config=config if config else None):
                ev_type = event.get("event")
                node_name = event.get("metadata", {}).get("langgraph_node")

                # 1. Step transitions
                if stream_steps and ev_type == "on_chain_start" and node_name in step_labels:
                    yield StepTransitionEvent(step=node_name)

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
                        yield TokenDeltaEvent(delta=delta_content)

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
                        candidate_resp = wf_def.to_workflow_output(final_output)
                        fallback_answer = candidate_resp.answer
                    except Exception:
                        pass
                if fallback_answer:
                    static_answer = str(fallback_answer)
                    tokens_delivered += 1
                    answer_chunks.append(static_answer)
                    yield TokenDeltaEvent(delta=static_answer)

            # 5. Build canonical WorkflowOutput
            resolved_tid = tr_extractor(handler) or trace_id
            domain_output: WorkflowOutput | None = None
            final_retrieved_contexts: list[str] = []
            if wf_def is not None:
                domain_output = wf_def.to_workflow_output(final_output, trace_id=resolved_tid)
                eval_ctx = wf_def.extract_eval_context(final_output)
                final_trace = accumulated_trace or domain_output.trace
                final_source_used = domain_output.source_used or (
                    getattr(eval_ctx, "source_used", None)
                    or (eval_ctx.get("source_used") if isinstance(eval_ctx, dict) else None)
                    or "direct"
                )
                final_citations = domain_output.citations
                final_rewritten_query = domain_output.rewritten_query or final_output.get(
                    "current_query", request.question
                )
                final_answer = domain_output.answer or str(final_output.get("answer") or "".join(answer_chunks))
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
                domain_output = WorkflowOutput(
                    workflow=getattr(request, "workflow", None) or "fix_single_jev",
                    answer=final_answer,
                    source_used=final_source_used,
                    trace=final_trace,
                    citations=final_citations,
                    rewritten_query=final_rewritten_query,
                    trace_id=resolved_tid,
                    session_id=getattr(request, "session_id", None),
                    variant_id=variant_id,
                    retrieved_contexts=final_retrieved_contexts,
                )

            # Ensure canonical domain_output has all resolved fields
            domain_output = domain_output.model_copy(
                update={
                    "answer": final_answer,
                    "source_used": final_source_used,
                    "trace": final_trace,
                    "citations": final_citations,
                    "rewritten_query": final_rewritten_query,
                    "trace_id": resolved_tid or domain_output.trace_id,
                    "session_id": getattr(request, "session_id", None) or domain_output.session_id,
                    "variant_id": domain_output.variant_id or variant_id,
                    "retrieved_contexts": final_retrieved_contexts,
                }
            )

            # Invoke lifecycle listeners (awaited before terminal event)
            await self._adispatch_listeners("completed", request, domain_output)

            # Yield canonical execution completed event
            yield ExecutionCompletedEvent(output=domain_output)

            logger.info(
                "chat_query_completed",
                workflow=getattr(request, "workflow", None) or (wf_def.info.id if wf_def else "fix_single_jev"),
                question=request.question,
                source_used=domain_output.source_used,
                trace=domain_output.trace,
                trace_id=domain_output.trace_id,
                variant_id=domain_output.variant_id,
            )

        except (asyncio.CancelledError, GeneratorExit):  # fmt: skip
            disconnect_trace = (
                accumulated_trace
                or (final_output.get("trace") if isinstance(final_output.get("trace"), list) else [])
                or []
            )
            partial_output = WorkflowOutput(
                workflow=getattr(request, "workflow", None) or (wf_def.info.id if wf_def else "unknown"),
                answer="".join(answer_chunks),
                source_used="client_disconnected",
                trace=disconnect_trace + ["Stream aborted by client disconnect"],
                session_id=getattr(request, "session_id", None),
                trace_id=tr_extractor(handler) or trace_id,
            )
            await self._adispatch_listeners("cancelled", request, partial_output)

            logger.warning(
                "Streaming cancelled by client disconnect",
                trace_id=trace_id,
                workflow=getattr(request, "workflow", None),
                question=request.question,
                source_used="client_disconnected",
                trace=disconnect_trace + ["Stream aborted by client disconnect"],
            )
            raise

        except Exception as exc:
            await self._adispatch_listeners("error", request, error=exc)
            logger.exception("Error during graph execution stream: %s", exc)
            # In the domain event model, graph failures directly emit ExecutionFailedEvent;
            # wf_def.handle_error() is invoked strictly for workflow telemetry/cleanup rather
            # than synthetic token fallback.
            if wf_def is not None:
                try:
                    wf_def.handle_error(exc, final_output or initial_state)
                except Exception as fallback_exc:
                    logger.error("Error in workflow.handle_error: %s", fallback_exc)

            yield ExecutionFailedEvent(error=str(exc))

        finally:
            if handler and hasattr(handler, "flush") and callable(handler.flush):
                try:
                    handler.flush()
                except Exception:
                    pass
            try:
                tel_flusher()
            except Exception:
                pass


_default_runner: WorkflowRunner | None = None


def get_workflow_runner(
    registry: WorkflowRegistry | None = None,
    *,
    binder: ExecutionContextBinder | None = None,
    evaluator: Evaluator | None = None,
    memory: ConversationMemoryAdapter | None = None,
    listeners: list[ExecutionLifecycleListener] | None = None,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
) -> WorkflowRunner:
    """Return the global default WorkflowRunner singleton or a customized instance."""
    global _default_runner
    if any(
        arg is not None
        for arg in (
            registry,
            binder,
            evaluator,
            memory,
            listeners,
            callback_factory,
            trace_extractor,
            telemetry_flusher,
            eval_task_fn,
        )
    ):
        runner = WorkflowRunner(
            registry=registry,
            binder=binder,
            evaluator=evaluator,
            memory=memory,
            listeners=listeners,
            callback_factory=callback_factory,
            trace_extractor=trace_extractor,
            telemetry_flusher=telemetry_flusher,
            eval_task_fn=eval_task_fn,
        )
        _default_runner = runner
        return runner
    if _default_runner is None:
        _default_runner = WorkflowRunner()
    return _default_runner


def reset_workflow_runner() -> None:
    """Reset the global default WorkflowRunner singleton (for test isolation)."""
    global _default_runner
    _default_runner = None


__all__ = [
    "DEFAULT_GENERATION_NODES",
    "DEFAULT_STEP_LABELS",
    "ExecutionResult",
    "WorkflowRunner",
    "_background_tasks",
    "get_workflow_runner",
    "is_tool_call_chunk",
    "reset_workflow_runner",
]
