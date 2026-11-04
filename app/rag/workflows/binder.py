"""Execution context binder seam preparing execution parameters, telemetry, and memory."""

import sys
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import structlog
from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from app.core.concurrency import run_async_safely
from app.core.config import get_settings
from app.core.telemetry import (
    extract_trace_id,
    get_langfuse_callback,
)
from app.rag.memory import (
    ConversationMemoryAdapter,
    NoOpMemoryStore,
)
from app.rag.workflows.base import (
    BoundExecutionContext,
    ExecutionProfile,
    WorkflowDefinition,
    WorkflowInput,
    parse_raw_messages,
)
from app.rag.workflows.registry import (
    WorkflowRegistry,
    get_workflow_registry,
)

logger = structlog.get_logger(__name__)


class ExecutionContextBinder:
    """Prepares execution state, 3-tier parameters, memory hydration, and telemetry context."""

    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        *,
        memory: ConversationMemoryAdapter | None = None,
        callback_factory: Callable[..., Any] | None = None,
        trace_extractor: Callable[[Any], str | None] | None = None,
        settings: Any | None = None,
    ) -> None:
        self._registry = registry
        self._memory = memory
        self._callback_factory = callback_factory
        self._trace_extractor = trace_extractor
        self._settings = settings

    @property
    def registry(self) -> WorkflowRegistry:
        """The active WorkflowRegistry instance."""
        if self._registry is None:
            self._registry = get_workflow_registry()
        return self._registry

    @property
    def memory(self) -> ConversationMemoryAdapter | None:
        """The active ConversationMemoryAdapter instance if configured."""
        return self._memory

    @property
    def callback_factory(self) -> Callable[..., Any]:
        """Injected Langfuse callback factory or canonical default."""
        if self._callback_factory is not None:
            return self._callback_factory
        runner_mod = sys.modules.get("app.rag.workflows.runner")
        if runner_mod is not None and hasattr(runner_mod, "get_langfuse_callback"):
            return getattr(runner_mod, "get_langfuse_callback")
        return get_langfuse_callback

    @property
    def trace_extractor(self) -> Callable[[Any], str | None]:
        """Injected trace extractor or canonical default."""
        if self._trace_extractor is not None:
            return self._trace_extractor
        runner_mod = sys.modules.get("app.rag.workflows.runner")
        if runner_mod is not None and hasattr(runner_mod, "extract_trace_id"):
            return getattr(runner_mod, "extract_trace_id")
        return extract_trace_id

    @property
    def settings(self) -> Any:
        """Application settings or default."""
        if self._settings is None:
            return get_settings()
        return self._settings

    def resolve_workflow(self, workflow_name: str) -> WorkflowDefinition:
        """Resolve workflow definition from registry, auto-registering defaults if empty."""
        if len(self.registry) == 0:
            from app.rag.workflows import register_default_workflows

            register_default_workflows(self.registry)

        try:
            return self.registry.get(workflow_name)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown workflow: {workflow_name}")

    def _resolve_parameters(
        self,
        request: Any,
        wf_def: WorkflowDefinition,
        profile: ExecutionProfile | None,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve parameters using 3-tier precedence: Explicit > Profile > Baseline."""
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

        return resolved_parameters

    async def _ahydrate_messages(self, request: Any) -> list[BaseMessage] | None:
        """Hydrate bounded historical messages from conversation memory store."""
        if self.memory is None or isinstance(self.memory, NoOpMemoryStore):
            return getattr(request, "messages", None)

        session_id = getattr(request, "session_id", None)
        if not session_id or not str(session_id).strip() or str(session_id).strip().lower() in ("undefined", "null"):
            return getattr(request, "messages", None)
        session_id = str(session_id).strip()

        rewind_turn_index = getattr(request, "rewind_turn_index", None)
        settings = self.settings
        max_turns = getattr(settings, "memory_max_turns", 10)

        hydrated: list[BaseMessage] | None = None
        if hasattr(self.memory, "aget_messages"):
            try:
                hydrated = await self.memory.aget_messages(
                    session_id,
                    limit=max_turns,
                    before_turn_index=rewind_turn_index,
                )
            except TypeError:
                hydrated = await self.memory.aget_messages(session_id)
                if rewind_turn_index is not None:
                    hydrated = hydrated[: rewind_turn_index * 2]
        elif hasattr(self.memory, "aget_turns"):
            try:
                turns = await self.memory.aget_turns(
                    session_id,
                    limit=max_turns,
                    before_turn_index=rewind_turn_index,
                )
            except TypeError:
                turns = await self.memory.aget_turns(session_id)
            if rewind_turn_index is not None:
                turns = turns[:rewind_turn_index]
            msgs: list[BaseMessage] = []
            for t in turns:
                if hasattr(t, "to_messages"):
                    msgs.extend(t.to_messages())
                else:
                    user_q = getattr(t, "user_query", getattr(t, "question", ""))
                    asst_a = getattr(t, "assistant_answer", getattr(t, "answer", ""))
                    msgs.extend([HumanMessage(content=user_q), AIMessage(content=asst_a)])
            hydrated = msgs

        turns_loaded = (len(hydrated) // 2) if hydrated else 0
        logger.info(
            "conversation_memory_hydrated",
            session_id=session_id,
            turns_loaded=turns_loaded,
            history_turns_loaded=turns_loaded,
            rewind_turn_index=rewind_turn_index,
        )
        if hydrated or rewind_turn_index is not None:
            return hydrated
        return getattr(request, "messages", None)

    def _hydrate_messages_sync(self, request: Any) -> list[BaseMessage] | None:
        """Synchronous wrapper for conversation memory hydration."""
        if self.memory is None or isinstance(self.memory, NoOpMemoryStore):
            return getattr(request, "messages", None)
        return run_async_safely(self._ahydrate_messages, request)

    def _to_workflow_input(
        self,
        request: Any,
        trace_id: str | None = None,
        variant_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        messages: Sequence[BaseMessage | dict[str, Any]] | None = None,
    ) -> WorkflowInput:
        """Ensure telemetry trace_id, variant_id, parameters, and messages are bound to domain WorkflowInput."""
        converter = getattr(request, "to_workflow_input", None)
        if callable(converter):
            request = converter()
        elif not isinstance(request, WorkflowInput):
            req_messages = messages if messages is not None else getattr(request, "messages", None)
            if req_messages:
                req_messages = [
                    m.model_dump() if isinstance(m, BaseModel) and not isinstance(m, BaseMessage) else m
                    for m in req_messages
                ]
            attachments = getattr(request, "attachments", None)
            if attachments:
                attachments = [a.model_dump() if isinstance(a, BaseModel) else a for a in attachments]
            request = WorkflowInput(
                question=getattr(request, "question", ""),
                workflow=getattr(request, "workflow", None),
                session_id=getattr(request, "session_id", None),
                rewind_turn_index=getattr(request, "rewind_turn_index", None),
                user_id=getattr(request, "user_id", None),
                trace_id=getattr(request, "trace_id", None),
                stream_steps=getattr(request, "stream_steps", True),
                messages=req_messages,
                attachments=attachments,
                variant_id=getattr(request, "variant_id", None),
                parameters=getattr(request, "parameters", None) or {},
            )

        eff_variant_id = variant_id or request.variant_id
        eff_parameters = parameters if parameters is not None else (request.parameters or {})
        eff_trace_id = trace_id or request.trace_id

        updates: dict[str, Any] = {}
        if eff_trace_id and request.trace_id != eff_trace_id:
            updates["trace_id"] = eff_trace_id
        if eff_variant_id and request.variant_id != eff_variant_id:
            updates["variant_id"] = eff_variant_id
        if eff_parameters and request.parameters != eff_parameters:
            updates["parameters"] = eff_parameters
        if messages is not None and request.messages != messages:
            updates["messages"] = messages
        if updates:
            return request.model_copy(update=updates)
        return request

    def _compile_telemetry(
        self,
        wf_name: str,
        wf_def: WorkflowDefinition,
        profile: ExecutionProfile | None,
        resolved_parameters: dict[str, Any],
        request: Any,
        *,
        callback: Any | None,
        metadata: dict[str, Any] | None,
        config: dict[str, Any] | None,
        streaming: bool,
        turn_count: int,
        history_turns_loaded: int,
        kwargs: dict[str, Any],
    ) -> tuple[str, Any, list[str], dict[str, Any]]:
        """Compile multi-dimensional tags, metadata, and initialize callback handler."""
        resolved_variant_id = (
            getattr(request, "variant_id", None)
            or kwargs.get("variant_id")
            or (profile.variant_id if profile else None)
        )
        resolved_model_name = resolved_parameters.get("model_name") or resolved_parameters.get("model")
        resolved_prompt_version = resolved_parameters.get("prompt_version")

        # 1. Multi-dimensional telemetry tags
        settings = self.settings
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
        if turn_count > 0:
            telemetry_tags.append(f"turn_count:{turn_count}")

        baseline_metadata = dict(wf_def.info.metadata) if getattr(wf_def.info, "metadata", None) else {}
        if isinstance(baseline_metadata.get("tags"), list):
            telemetry_tags.extend(baseline_metadata["tags"])
        if profile and profile.tags:
            telemetry_tags.extend(profile.tags)
        if kwargs.get("tags") and isinstance(kwargs["tags"], list):
            telemetry_tags.extend(kwargs["tags"])
        req_tags = getattr(request, "tags", None) or (request.get("tags") if isinstance(request, dict) else None)
        if req_tags and isinstance(req_tags, list):
            telemetry_tags.extend(req_tags)
        if config and "tags" in config and isinstance(config["tags"], list):
            telemetry_tags.extend(config["tags"])

        # Deduplicate tags preserving order
        seen_tags: set[str] = set()
        deduped_tags: list[str] = []
        for t in telemetry_tags:
            if t not in seen_tags:
                seen_tags.add(t)
                deduped_tags.append(t)
        telemetry_tags = deduped_tags

        # 2. Telemetry metadata (3-tier resolution)
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
        telemetry_metadata["turn_count"] = turn_count
        telemetry_metadata["history_turns_loaded"] = history_turns_loaded
        if streaming:
            telemetry_metadata["streaming"] = True

        raw_tid = getattr(request, "trace_id", None)
        sanitized_tid = raw_tid.replace("-", "").lower() if raw_tid else (uuid.uuid4().hex if streaming else None)

        if callback is not None:
            handler = callback
        else:
            cb_factory = self.callback_factory
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

        trace_id = self.trace_extractor(handler) or sanitized_tid
        if not trace_id:
            ctx_tid = structlog.contextvars.get_contextvars().get("trace_id")
            trace_id = ctx_tid.replace("-", "").lower() if ctx_tid else uuid.uuid4().hex

        if handler is not None:
            if hasattr(handler, "_trace_context") and isinstance(handler._trace_context, dict):
                handler._trace_context.setdefault("trace_id", trace_id)
            elif not hasattr(handler, "last_trace_id") or handler.last_trace_id is None:
                try:
                    handler.last_trace_id = trace_id
                except Exception:
                    pass

        telemetry_metadata["trace_id"] = trace_id
        return trace_id, handler, telemetry_tags, telemetry_metadata

    def _build_bound_context(
        self,
        request: Any,
        *,
        streaming: bool,
        callback: Any | None,
        metadata: dict[str, Any] | None,
        config: dict[str, Any] | None,
        hydrated_messages: Sequence[BaseMessage | dict[str, Any]] | None,
        kwargs: dict[str, Any],
    ) -> BoundExecutionContext:
        """Internal procedure orchestrating parameter resolution, telemetry, and runnable config."""
        wf_name = (request.workflow if hasattr(request, "workflow") and request.workflow else None) or kwargs.get(
            "workflow", "fix_single_jev"
        )
        wf_def = self.resolve_workflow(wf_name)

        req_variant_id = getattr(request, "variant_id", None) or kwargs.get("variant_id")
        profile: ExecutionProfile | None = None
        if req_variant_id:
            profile = self.registry.get_profile(wf_def.info.id, req_variant_id)
        resolved_variant_id = req_variant_id or (profile.variant_id if profile else None)

        # 3-Tier Precedence Resolution for Parameters
        resolved_parameters = self._resolve_parameters(request, wf_def, profile, kwargs)

        effective_messages = hydrated_messages if hydrated_messages is not None else getattr(request, "messages", None)
        turn_count = (len(effective_messages) // 2) if effective_messages is not None else 0
        history_turns_loaded = turn_count if getattr(request, "session_id", None) else 0

        # Telemetry compilation
        trace_id, handler, _tags, _telemetry_metadata = self._compile_telemetry(
            wf_name,
            wf_def,
            profile,
            resolved_parameters,
            request,
            callback=callback,
            metadata=metadata,
            config=config,
            streaming=streaming,
            turn_count=turn_count,
            history_turns_loaded=history_turns_loaded,
            kwargs=kwargs,
        )

        # Contextvars binding
        context_bindings: dict[str, Any] = {"workflow": wf_name, "trace_id": trace_id}
        session_id = getattr(request, "session_id", None) or (
            request.get("session_id") if isinstance(request, dict) else None
        )
        if session_id:
            context_bindings["session_id"] = session_id
        user_id = getattr(request, "user_id", None) or (request.get("user_id") if isinstance(request, dict) else None)
        if user_id:
            context_bindings["user_id"] = user_id
        if resolved_variant_id:
            context_bindings["variant_id"] = resolved_variant_id
        context_bindings["turn_count"] = turn_count
        context_bindings["history_turns_loaded"] = history_turns_loaded
        structlog.contextvars.bind_contextvars(**context_bindings)

        # Initial state preparation
        wf_input = self._to_workflow_input(
            request,
            trace_id=trace_id,
            variant_id=resolved_variant_id,
            parameters=resolved_parameters,
            messages=effective_messages,
        )
        initial_state = wf_def.to_initial_state(wf_input, trace_id=trace_id)
        if resolved_variant_id and "variant_id" not in initial_state:
            initial_state["variant_id"] = resolved_variant_id

        # RunnableConfig preparation
        if config is not None:
            run_config = dict(config)
            if handler and "callbacks" not in run_config:
                run_config["callbacks"] = [handler]
        else:
            run_config = {}
            if handler:
                run_config["callbacks"] = [handler]

        cfg_meta: dict[str, Any] = {"trace_id": trace_id}
        if streaming:
            cfg_meta["streaming"] = True
        if metadata:
            cfg_meta.update(metadata)
        run_metadata = dict(run_config.get("metadata", {}))
        run_metadata.update(cfg_meta)
        run_config["metadata"] = run_metadata

        if kwargs.get("tags"):
            run_config["tags"] = kwargs["tags"]
        elif config and "tags" in config:
            run_config["tags"] = config["tags"]

        configurable = dict(run_config.get("configurable", {}))
        thread_id = session_id or uuid.uuid4().hex
        configurable.setdefault("thread_id", thread_id)
        if resolved_variant_id:
            configurable.setdefault("variant_id", resolved_variant_id)
        configurable.update(resolved_parameters)
        run_config["configurable"] = configurable

        parsed_messages = parse_raw_messages(effective_messages) if effective_messages is not None else None

        return BoundExecutionContext(
            workflow=wf_def,
            initial_state=initial_state,
            config=run_config,
            trace_id=trace_id,
            session_id=session_id,
            variant_id=resolved_variant_id,
            resolved_parameters=resolved_parameters,
            hydrated_messages=parsed_messages,
        )

    def bind(
        self,
        request: WorkflowInput,
        *,
        streaming: bool = False,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        hydrated_messages: Sequence[BaseMessage | dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> BoundExecutionContext:
        """Synchronously bind request, parameters, memory, and telemetry into BoundExecutionContext."""
        if hydrated_messages is None and getattr(request, "session_id", None):
            hydrated_messages = self._hydrate_messages_sync(request)
        return self._build_bound_context(
            request,
            streaming=streaming,
            callback=callback,
            metadata=metadata,
            config=config,
            hydrated_messages=hydrated_messages,
            kwargs=kwargs,
        )

    async def abind(
        self,
        request: WorkflowInput,
        *,
        streaming: bool = False,
        callback: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        hydrated_messages: Sequence[BaseMessage | dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> BoundExecutionContext:
        """Asynchronously bind request, parameters, memory, and telemetry into BoundExecutionContext."""
        if hydrated_messages is None and getattr(request, "session_id", None):
            hydrated_messages = await self._ahydrate_messages(request)
        return self._build_bound_context(
            request,
            streaming=streaming,
            callback=callback,
            metadata=metadata,
            config=config,
            hydrated_messages=hydrated_messages,
            kwargs=kwargs,
        )


__all__ = ["ExecutionContextBinder"]
