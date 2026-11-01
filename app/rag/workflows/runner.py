"""WorkflowRunner deep execution and streaming pipeline module."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog
from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from app.api.schemas import (
        ChatRequest,
        ChatResponse,
    )
from app.core.config import get_settings
from app.core.telemetry import (
    extract_trace_id,
    flush_telemetry,
    get_langfuse_callback,
)
from app.rag.evaluation import Evaluator, get_evaluator
from app.rag.workflows.base import (
    EvalContext,
    ExecutionProfile,
    WorkflowDefinition,
    WorkflowInput,
    WorkflowOutput,
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

    response: Any
    eval_context: EvalContext


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

    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        *,
        evaluator: Evaluator | None = None,
        callback_factory: Callable[..., Any] | None = None,
        trace_extractor: Callable[[Any], str | None] | None = None,
        telemetry_flusher: Callable[[], None] | None = None,
        eval_task_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else get_workflow_registry()
        self._evaluator = evaluator
        self._callback_factory = callback_factory
        self._trace_extractor = trace_extractor
        self._telemetry_flusher = telemetry_flusher
        self._eval_task_fn = eval_task_fn
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @property
    def eval_task_fn(self) -> Callable[..., Any] | None:
        return self._eval_task_fn

    @eval_task_fn.setter
    def eval_task_fn(self, val: Callable[..., Any] | None) -> None:
        self._eval_task_fn = val

    @property
    def evaluator(self) -> Evaluator:
        return self._evaluator or get_evaluator()

    @evaluator.setter
    def evaluator(self, val: Evaluator | None) -> None:
        self._evaluator = val

    @property
    def callback_factory(self) -> Callable[..., Any]:
        return self._callback_factory or get_langfuse_callback

    @callback_factory.setter
    def callback_factory(self, val: Callable[..., Any] | None) -> None:
        self._callback_factory = val

    @property
    def trace_extractor(self) -> Callable[[Any], str | None]:
        return self._trace_extractor or extract_trace_id

    @trace_extractor.setter
    def trace_extractor(self, val: Callable[[Any], str | None] | None) -> None:
        self._trace_extractor = val

    @property
    def telemetry_flusher(self) -> Callable[[], None]:
        return self._telemetry_flusher or flush_telemetry

    @telemetry_flusher.setter
    def telemetry_flusher(self, val: Callable[[], None] | None) -> None:
        self._telemetry_flusher = val

    def format_sse(self, event: str, data: dict[str, Any] | BaseModel) -> str:
        """Format an event frame into SSE string protocol."""
        return format_sse(event, data)

    def is_tool_call_chunk(self, chunk: Any) -> bool:
        """Filter out token deltas when chunk contains tool call chunks or structured calls."""
        return is_tool_call_chunk(chunk)

    def _resolve_workflow(self, workflow_name: str) -> WorkflowDefinition:
        """Resolve workflow definition from registry, auto-registering defaults if empty."""
        if len(self.registry) == 0:
            from app.rag.workflows import register_default_workflows

            register_default_workflows(self.registry)

        try:
            return self.registry.get(workflow_name)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown workflow: {workflow_name}")

    def _to_workflow_input(
        self,
        request: ChatRequest | WorkflowInput,
        trace_id: str | None = None,
        variant_id: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> WorkflowInput:
        """Bridge inbound transport ChatRequest or WorkflowInput to domain WorkflowInput."""
        eff_variant_id = variant_id or getattr(request, "variant_id", None)
        eff_parameters = (
            parameters
            if parameters is not None
            else (getattr(request, "parameters", None) or {})
        )
        eff_trace_id = trace_id or getattr(request, "trace_id", None)
        eff_workflow = getattr(request, "workflow", "fix_single_jev")

        if isinstance(request, WorkflowInput):
            updates: dict[str, Any] = {}
            if eff_trace_id and request.trace_id != eff_trace_id:
                updates["trace_id"] = eff_trace_id
            if eff_variant_id and request.variant_id != eff_variant_id:
                updates["variant_id"] = eff_variant_id
            if eff_parameters and request.parameters != eff_parameters:
                updates["parameters"] = eff_parameters
            if updates:
                return request.model_copy(update=updates)
            return request

        raw_msgs = getattr(request, "messages", None)
        messages = None
        if raw_msgs:
            messages = [
                m.model_dump()
                if hasattr(m, "model_dump")
                else (dict(m) if isinstance(m, dict) else {"role": getattr(m, "role", ""), "content": getattr(m, "content", "")})
                for m in raw_msgs
            ]

        raw_att = getattr(request, "attachments", None)
        attachments = None
        if raw_att:
            attachments = [
                a.model_dump() if hasattr(a, "model_dump") else (dict(a) if isinstance(a, dict) else dict(a))
                for a in raw_att
            ]

        return WorkflowInput(
            question=request.question,
            workflow=eff_workflow,
            messages=messages,
            attachments=attachments,
            session_id=getattr(request, "session_id", None),
            user_id=getattr(request, "user_id", None),
            trace_id=eff_trace_id,
            variant_id=eff_variant_id,
            parameters=eff_parameters,
        )

    def _to_chat_response(
        self,
        output: WorkflowOutput | ChatResponse,
        trace_id: str | None = None,
    ) -> ChatResponse:
        """Bridge domain WorkflowOutput or ChatResponse to transport ChatResponse."""
        from app.api.schemas import ChatResponse

        if isinstance(output, ChatResponse):
            if trace_id and not output.trace_id:
                output.trace_id = trace_id
            return output

        resolved_tid = trace_id or getattr(output, "trace_id", None)
        resolved_vid = getattr(output, "variant_id", None)
        return ChatResponse(
            workflow=output.workflow,
            answer=output.answer,
            source_used=output.source_used,
            citations=output.citations,
            trace=output.trace,
            rewritten_query=output.rewritten_query,
            trace_id=resolved_tid,
            variant_id=resolved_vid,
        )

    def _adapt_workflow_output(
        self,
        wf_def: WorkflowDefinition,
        final_state: dict[str, Any],
        trace_id: str | None = None,
    ) -> WorkflowOutput:
        """Extract domain WorkflowOutput from workflow instance adapting either to_workflow_output or to_chat_response."""
        variant_id = final_state.get("variant_id") if isinstance(final_state, dict) else None
        if hasattr(wf_def, "to_workflow_output"):
            out = wf_def.to_workflow_output(final_state, trace_id=trace_id)
            if isinstance(out, WorkflowOutput):
                if not out.variant_id and variant_id:
                    return out.model_copy(update={"variant_id": variant_id})
                return out
            return WorkflowOutput(
                workflow=getattr(out, "workflow", wf_def.info.id),
                answer=getattr(out, "answer", ""),
                source_used=getattr(out, "source_used", "direct"),
                citations=getattr(out, "citations", []),
                trace=getattr(out, "trace", []),
                rewritten_query=getattr(out, "rewritten_query", None),
                trace_id=getattr(out, "trace_id", trace_id),
                variant_id=getattr(out, "variant_id", None) or variant_id,
            )
        if hasattr(wf_def, "to_chat_response"):
            resp = wf_def.to_chat_response(final_state, trace_id=trace_id)
            return WorkflowOutput(
                workflow=getattr(resp, "workflow", wf_def.info.id),
                answer=getattr(resp, "answer", ""),
                source_used=getattr(resp, "source_used", "direct"),
                citations=getattr(resp, "citations", []),
                trace=getattr(resp, "trace", []),
                rewritten_query=getattr(resp, "rewritten_query", None),
                trace_id=getattr(resp, "trace_id", trace_id),
                variant_id=getattr(resp, "variant_id", None) or variant_id,
            )
        raise AttributeError(f"Workflow '{wf_def}' does not implement to_workflow_output or to_chat_response")

    def _prepare_execution(
        self,
        request: ChatRequest | WorkflowInput,
        streaming: bool = False,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[WorkflowDefinition, str | None, Any, dict[str, Any], dict[str, Any]]:
        """Resolve workflow definition, initialize telemetry contextvars, and prepare initial state."""
        wf_name = getattr(request, "workflow", None) or kwargs.get("workflow", "fix_single_jev")
        wf_def = self._resolve_workflow(wf_name)

        cb_factory = self.callback_factory
        tr_extractor = self.trace_extractor

        # 1. Resolve variant_id (Explicit overrides > None)
        req_variant_id = getattr(request, "variant_id", None) or kwargs.get("variant_id")

        # 2. Look up ExecutionProfile from registry if variant_id present
        profile: ExecutionProfile | None = None
        if req_variant_id:
            profile = self.registry.get_profile(wf_def.info.id, req_variant_id)

        resolved_variant_id = req_variant_id or (profile.variant_id if profile else None)

        # 3. 3-Tier Precedence Resolution for Parameters:
        # Precedence: Explicit Overrides (Tier 1) > Registered Profile (Tier 2) > Workflow Baseline (Tier 3)
        resolved_parameters: dict[str, Any] = {}

        # Tier 3: Workflow Baseline
        baseline_metadata = dict(wf_def.info.metadata) if getattr(wf_def.info, "metadata", None) else {}
        if "parameters" in baseline_metadata and isinstance(baseline_metadata["parameters"], dict):
            resolved_parameters.update(baseline_metadata["parameters"])
        for k in ("model_name", "model", "prompt_version", "temperature"):
            if k in baseline_metadata and baseline_metadata[k] is not None:
                resolved_parameters[k] = baseline_metadata[k]

        # Tier 2: Registered Profile
        if profile is not None:
            if profile.parameters:
                resolved_parameters.update(profile.parameters)
            if profile.model_name is not None:
                resolved_parameters["model_name"] = profile.model_name
            if profile.prompt_version is not None:
                resolved_parameters["prompt_version"] = profile.prompt_version
            if profile.temperature is not None:
                resolved_parameters["temperature"] = profile.temperature

        # Tier 1: Explicit Overrides
        req_params = getattr(request, "parameters", None)
        if req_params and isinstance(req_params, dict):
            resolved_parameters.update(req_params)
        if "parameters" in kwargs and isinstance(kwargs["parameters"], dict):
            resolved_parameters.update(kwargs["parameters"])
        for k in ("model_name", "model", "prompt_version", "temperature"):
            if k in kwargs and kwargs[k] is not None:
                resolved_parameters[k] = kwargs[k]

        resolved_model_name = resolved_parameters.get("model_name") or resolved_parameters.get("model")
        resolved_prompt_version = resolved_parameters.get("prompt_version")

        # 4. Multi-dimensional telemetry tags
        settings = get_settings()
        app_env = getattr(settings, "app_env", "development")

        telemetry_tags: list[str] = [
            f"workflow:{wf_def.info.id}",
            f"env:{app_env}",
        ]
        if resolved_variant_id:
            telemetry_tags.append(f"variant:{resolved_variant_id}")
        if resolved_model_name:
            telemetry_tags.append(f"model:{resolved_model_name}")
        if resolved_prompt_version:
            telemetry_tags.append(f"prompt:{resolved_prompt_version}")

        # Additional tags from baseline, profile, explicit kwargs/config
        if isinstance(baseline_metadata.get("tags"), list):
            telemetry_tags.extend(baseline_metadata["tags"])
        if profile and profile.tags:
            telemetry_tags.extend(profile.tags)
        if kwargs.get("tags") and isinstance(kwargs["tags"], list):
            telemetry_tags.extend(kwargs["tags"])
        if getattr(request, "tags", None) and isinstance(request.tags, list):
            telemetry_tags.extend(request.tags)
        if config and "tags" in config and isinstance(config["tags"], list):
            telemetry_tags.extend(config["tags"])

        # Deduplicate preserving order
        seen_tags: set[str] = set()
        deduped_tags: list[str] = []
        for t in telemetry_tags:
            if t not in seen_tags:
                seen_tags.add(t)
                deduped_tags.append(t)
        telemetry_tags = deduped_tags

        # 5. Telemetry metadata (3-tier resolution)
        telemetry_metadata: dict[str, Any] = {}
        if baseline_metadata:
            telemetry_metadata.update(baseline_metadata)
        if profile and profile.metadata:
            telemetry_metadata.update(profile.metadata)
        if metadata:
            telemetry_metadata.update(metadata)
        if kwargs.get("metadata") and isinstance(kwargs["metadata"], dict):
            telemetry_metadata.update(kwargs["metadata"])

        if resolved_variant_id:
            telemetry_metadata["variant_id"] = resolved_variant_id
        if resolved_model_name:
            telemetry_metadata["model_name"] = resolved_model_name
        if resolved_prompt_version:
            telemetry_metadata["prompt_version"] = resolved_prompt_version
        if resolved_parameters:
            telemetry_metadata["parameters"] = resolved_parameters
        if streaming:
            telemetry_metadata["streaming"] = True

        raw_tid = getattr(request, "trace_id", None)
        sanitized_tid = (
            raw_tid.replace("-", "").lower() if raw_tid else (uuid.uuid4().hex if streaming else None)
        )

        if callback is not None:
            handler = callback
        else:
            try:
                handler = cb_factory(
                    workflow_name=wf_name,
                    session_id=getattr(request, "session_id", None),
                    user_id=getattr(request, "user_id", None),
                    trace_id=sanitized_tid,
                    tags=telemetry_tags,
                    metadata=telemetry_metadata,
                )
            except TypeError:
                handler = cb_factory(
                    workflow_name=wf_name,
                    session_id=getattr(request, "session_id", None),
                    user_id=getattr(request, "user_id", None),
                    trace_id=sanitized_tid,
                    metadata=telemetry_metadata,
                )

        trace_id = tr_extractor(handler) or sanitized_tid
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

        if not trace_id and (streaming or raw_tid):
            ctx_tid = structlog.contextvars.get_contextvars().get("trace_id")
            trace_id = ctx_tid.replace("-", "").lower() if ctx_tid else uuid.uuid4().hex

        if trace_id:
            telemetry_metadata["trace_id"] = trace_id

        context_bindings: dict[str, Any] = {"workflow": wf_name}
        if trace_id:
            context_bindings["trace_id"] = trace_id
        if getattr(request, "session_id", None):
            context_bindings["session_id"] = request.session_id
        if getattr(request, "user_id", None):
            context_bindings["user_id"] = request.user_id
        if resolved_variant_id:
            context_bindings["variant_id"] = resolved_variant_id
        structlog.contextvars.bind_contextvars(**context_bindings)

        wf_input = self._to_workflow_input(
            request,
            trace_id=trace_id,
            variant_id=resolved_variant_id,
            parameters=resolved_parameters,
        )
        initial_state = wf_def.to_initial_state(wf_input, trace_id=trace_id)
        if resolved_variant_id and "variant_id" not in initial_state:
            initial_state["variant_id"] = resolved_variant_id

        # 6. RunnableConfig preparation
        if config is not None:
            run_config = dict(config)
            if handler and "callbacks" not in run_config:
                run_config["callbacks"] = [handler]
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
            run_config.setdefault("metadata", {}).update(cfg_meta)

        if kwargs.get("tags"):
            run_config["tags"] = kwargs["tags"]
        elif config and "tags" in config:
            run_config["tags"] = config["tags"]

        configurable = dict(run_config.get("configurable", {}))
        thread_id = getattr(request, "session_id", None) or uuid.uuid4().hex
        configurable.setdefault("thread_id", thread_id)
        if resolved_variant_id:
            configurable.setdefault("variant_id", resolved_variant_id)
        configurable.update(resolved_parameters)
        run_config["configurable"] = configurable

        return wf_def, trace_id, handler, initial_state, run_config

    async def _safe_evaluate(
        self,
        *,
        trace_id: str,
        question: str,
        answer: str,
        source_used: str,
        retrieved_contexts: list[str],
    ) -> None:
        """Shielded online evaluation execution delegating to injected evaluator or hooks."""
        try:
            if self.eval_task_fn is not None:
                res = self.eval_task_fn(
                    trace_id=trace_id,
                    question=question,
                    answer=answer,
                    source_used=source_used,
                    retrieved_contexts=retrieved_contexts,
                )
            else:
                res = self.evaluator.aevaluate(
                    question=question,
                    answer=answer,
                    source_used=source_used,
                    retrieved_contexts=retrieved_contexts,
                    trace_id=trace_id,
                )
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                await res
        except Exception as exc:
            logger.warning(
                "online_evaluation_failed_shielded",
                trace_id=trace_id,
                error=str(exc),
            )

    def _finalize_execution(
        self,
        wf_def: WorkflowDefinition,
        request: ChatRequest | WorkflowInput,
        final_state: dict[str, Any],
        trace_id: str | None,
        handler: Any,
        background_tasks: BackgroundTasks | None = None,
    ) -> ChatResponse:
        """Convert final state to ChatResponse, bind resolved trace context, and schedule online evaluation."""
        tr_extractor = self.trace_extractor

        domain_output = self._adapt_workflow_output(wf_def, final_state, trace_id=trace_id)
        chat_response = self._to_chat_response(domain_output, trace_id=trace_id)
        resolved_tid = tr_extractor(handler) or trace_id or chat_response.trace_id

        if resolved_tid:
            structlog.contextvars.bind_contextvars(trace_id=resolved_tid)
            if not chat_response.trace_id:
                chat_response.trace_id = resolved_tid
            if isinstance(final_state, dict) and "trace_id" not in final_state and request.trace_id:
                final_state["trace_id"] = resolved_tid

        if not chat_response.variant_id:
            chat_response.variant_id = (
                final_state.get("variant_id") if isinstance(final_state, dict) else None
            ) or getattr(request, "variant_id", None)

        logger.info(
            "chat_query_completed",
            workflow=getattr(request, "workflow", "fix_single_jev"),
            question=request.question,
            source_used=chat_response.source_used,
            trace=chat_response.trace,
            trace_id=resolved_tid,
            variant_id=chat_response.variant_id,
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
                try:
                    background_tasks.add_task(
                        self._safe_evaluate,
                        trace_id=resolved_tid,
                        question=request.question,
                        answer=chat_response.answer,
                        source_used=source_used,
                        retrieved_contexts=retrieved_contexts,
                    )
                except Exception as exc:
                    logger.warning(
                        "online_evaluation_scheduling_failed",
                        trace_id=resolved_tid,
                        error=str(exc),
                    )
            else:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop is not None and loop.is_running():
                    try:
                        task = loop.create_task(
                            self._safe_evaluate(
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
                    except Exception as exc:
                        logger.warning(
                            "online_evaluation_scheduling_failed",
                            trace_id=resolved_tid,
                            error=str(exc),
                        )
                else:
                    logger.debug("No active event loop for background evaluation scheduling")

        if handler is not None and hasattr(handler, "flush") and callable(handler.flush):
            try:
                handler.flush()
            except Exception:
                pass

        return chat_response

    def _execute_with_state(
        self,
        request: ChatRequest | WorkflowInput,
        background_tasks: BackgroundTasks | None = None,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[ChatResponse, dict[str, Any]]:
        """Synchronously execute workflow and return (ChatResponse, final_state) without model mutation."""
        try:
            wf_def, trace_id, handler, initial_state, run_config = self._prepare_execution(
                request,
                callback=callback,
                metadata=metadata,
                config=config,
                **kwargs,
            )
            variant_id = initial_state.get("variant_id") or run_config.get("configurable", {}).get("variant_id")
            try:
                graph = wf_def.get_compiled_graph(variant_id=variant_id)
            except TypeError:
                graph = wf_def.get_compiled_graph()

            try:
                final_state = graph.invoke(initial_state, config=run_config if run_config else None)
                if not isinstance(final_state, dict):
                    final_state = {"answer": str(final_state)}
            except Exception as exc:
                logger.exception("Error executing workflow %s: %s", getattr(request, "workflow", "unknown"), exc)
                final_state = wf_def.handle_error(exc, initial_state)

            response = self._finalize_execution(
                wf_def=wf_def,
                request=request,
                final_state=final_state,
                trace_id=trace_id,
                handler=handler,
                background_tasks=background_tasks,
            )
            return response, final_state
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Error in chat pipeline: %s", exc)
            raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc
        finally:
            try:
                self.telemetry_flusher()
            except Exception:
                pass

    def execute(
        self,
        request: ChatRequest | WorkflowInput,
        background_tasks: BackgroundTasks | None = None,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Synchronously execute a registered workflow and return ChatResponse."""
        response, _ = self._execute_with_state(
            request,
            background_tasks=background_tasks,
            callback=callback,
            metadata=metadata,
            config=config,
            **kwargs,
        )
        return response

    def execute_with_context(
        self,
        request: ChatRequest | WorkflowInput,
        background_tasks: BackgroundTasks | None = None,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Public execution entrypoint returning both ChatResponse and normalized EvalContext."""
        response, final_state = self._execute_with_state(
            request,
            background_tasks=background_tasks,
            callback=callback,
            metadata=metadata,
            config=config,
            **kwargs,
        )
        wf_def = self._resolve_workflow(request.workflow)
        eval_context = wf_def.extract_eval_context(final_state)
        return ExecutionResult(response=response, eval_context=eval_context)

    async def _aexecute_with_state(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[ChatResponse, dict[str, Any]]:
        """Asynchronously execute workflow and return (ChatResponse, final_state) without model mutation."""
        try:
            wf_def, trace_id, handler, initial_state, run_config = self._prepare_execution(
                request,
                callback=callback,
                metadata=metadata,
                config=config,
                **kwargs,
            )
            variant_id = initial_state.get("variant_id") or run_config.get("configurable", {}).get("variant_id")
            try:
                graph = wf_def.get_compiled_graph(variant_id=variant_id)
            except TypeError:
                graph = wf_def.get_compiled_graph()

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
                logger.exception("Error executing workflow %s: %s", request.workflow, exc)
                final_state = wf_def.handle_error(exc, initial_state)

            response = self._finalize_execution(
                wf_def=wf_def,
                request=request,
                final_state=final_state,
                trace_id=trace_id,
                handler=handler,
                background_tasks=background_tasks,
            )
            return response, final_state
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Error in chat pipeline: %s", exc)
            raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc
        finally:
            try:
                self.telemetry_flusher()
            except Exception:
                pass

    async def aexecute(
        self,
        request: ChatRequest,
        background_tasks: BackgroundTasks | None = None,
        *,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Asynchronously execute a registered workflow and return ChatResponse."""
        response, _ = await self._aexecute_with_state(
            request,
            background_tasks=background_tasks,
            callback=callback,
            metadata=metadata,
            config=config,
            **kwargs,
        )
        return response

    def stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[str]:
        """Validate workflow and return an asynchronous generator yielding pre-formatted SSE frames."""
        self._resolve_workflow(request.workflow)
        return self._stream_generator(request)

    async def _stream_generator(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[str]:
        """Consume LangGraph astream_events v2 and yield pre-formatted SSE event strings."""
        from app.api.schemas import (
            StreamErrorEvent,
            StreamMetadataEvent,
            StreamStepEvent,
            StreamTokenEvent,
        )

        cb_factory = self.callback_factory
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
            wf_def, trace_id, handler, initial_state, config = self._prepare_execution(
                request,
                streaming=True,
            )
            variant_id = initial_state.get("variant_id") or config.get("configurable", {}).get("variant_id")
            try:
                graph = wf_def.get_compiled_graph(variant_id=variant_id)
            except TypeError:
                graph = wf_def.get_compiled_graph()
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
                    yield self.format_sse(
                        "step",
                        StreamStepEvent(
                            step=node_name,
                            label=step_labels[node_name],
                            timestamp=datetime.now(UTC).timestamp(),
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
                        candidate_resp = self._adapt_workflow_output(wf_def, final_output)
                        fallback_answer = candidate_resp.answer
                    except Exception:
                        pass
                if fallback_answer:
                    static_answer = str(fallback_answer)
                    tokens_delivered += 1
                    answer_chunks.append(static_answer)
                    yield self.format_sse("token", StreamTokenEvent(delta=static_answer))

            # 5. Terminal Metadata Event
            resolved_tid = tr_extractor(handler) or trace_id
            if wf_def is not None:
                domain_output = self._adapt_workflow_output(wf_def, final_output, trace_id=resolved_tid)
                chat_resp = self._to_chat_response(domain_output, trace_id=resolved_tid)
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
                "workflow": getattr(request, "workflow", "fix_single_jev"),
                "source_used": final_source_used,
                "citations": final_citations,
                "trace": final_trace,
                "rewritten_query": final_rewritten_query,
                "variant_id": (chat_resp.variant_id if wf_def is not None else None) or variant_id,
            }
            try:
                yield self.format_sse("metadata", StreamMetadataEvent(**metadata_dict))
            except Exception:
                yield self.format_sse("metadata", metadata_dict)

            # Safeguard background evaluation task
            if resolved_tid:
                try:
                    eval_task = asyncio.create_task(
                        self._safe_evaluate(
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
                except Exception as exc:
                    logger.warning(
                        "online_evaluation_scheduling_failed",
                        trace_id=resolved_tid,
                        error=str(exc),
                    )

            logger.info(
                "chat_query_completed",
                workflow=getattr(request, "workflow", "fix_single_jev"),
                question=request.question,
                source_used=final_source_used,
                trace=final_trace,
                trace_id=resolved_tid,
                variant_id=metadata_dict.get("variant_id"),
            )

        except asyncio.CancelledError, GeneratorExit:
            disconnect_trace = (
                accumulated_trace
                or (final_output.get("trace") if isinstance(final_output.get("trace"), list) else [])
                or []
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
            fallback_state: dict[str, Any] | None = None
            if wf_def is not None:
                try:
                    fallback_state = wf_def.handle_error(exc, final_output or initial_state)
                    logger.info(
                        "workflow_error_fallback_invoked",
                        workflow=request.workflow,
                        fallback_answer=fallback_state.get("answer") if isinstance(fallback_state, dict) else None,
                    )
                except Exception as fallback_exc:
                    logger.error("Error in workflow.handle_error: %s", fallback_exc)

            if isinstance(fallback_state, dict):
                fallback_answer = fallback_state.get("answer")
                if fallback_answer and tokens_delivered == 0:
                    yield self.format_sse("token", StreamTokenEvent(delta=str(fallback_answer)))
                    tokens_delivered += 1

                resolved_tid = tr_extractor(handler) or trace_id or fallback_state.get("trace_id")
                err_trace = (accumulated_trace or []) + fallback_state.get("trace", [f"Error: {str(exc)}"])
                err_metadata = {
                    "trace_id": resolved_tid,
                    "workflow": request.workflow,
                    "source_used": fallback_state.get("source_used", "error_fallback"),
                    "citations": fallback_state.get("citations", []),
                    "trace": err_trace,
                    "rewritten_query": fallback_state.get("current_query", request.question),
                }
                try:
                    yield self.format_sse("metadata", StreamMetadataEvent(**err_metadata))
                except Exception:
                    yield self.format_sse("metadata", err_metadata)

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
                tel_flusher()
            except Exception:
                pass


_default_runner: WorkflowRunner | None = None


def get_workflow_runner(
    registry: WorkflowRegistry | None = None,
    *,
    evaluator: Evaluator | None = None,
    callback_factory: Callable[..., Any] | None = None,
    trace_extractor: Callable[[Any], str | None] | None = None,
    telemetry_flusher: Callable[[], None] | None = None,
    eval_task_fn: Callable[..., Any] | None = None,
) -> WorkflowRunner:
    """Return the global default WorkflowRunner singleton or a customized instance."""
    global _default_runner
    if any(
        arg is not None
        for arg in (registry, evaluator, callback_factory, trace_extractor, telemetry_flusher, eval_task_fn)
    ):
        return WorkflowRunner(
            registry=registry,
            evaluator=evaluator,
            callback_factory=callback_factory,
            trace_extractor=trace_extractor,
            telemetry_flusher=telemetry_flusher,
            eval_task_fn=eval_task_fn,
        )
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
    "format_sse",
    "get_workflow_runner",
    "is_tool_call_chunk",
    "reset_workflow_runner",
]
