import logging
import uuid
from typing import Any

import structlog
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_langfuse_client: Langfuse | None = None


def extract_llm_token_counts(response: Any) -> dict[str, int]:
    """Extract input, output, and total token counts from an LLMResult across providers."""
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        usage = llm_output.get("token_usage") or llm_output.get("usage")
        if isinstance(usage, dict):
            input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            total_tokens = usage.get("total_tokens") or (input_tokens + output_tokens)

    if not (input_tokens or output_tokens):
        generations = getattr(response, "generations", None) or []
        for gen_list in generations:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                usage_meta = getattr(msg, "usage_metadata", None)
                if isinstance(usage_meta, dict):
                    input_tokens = usage_meta.get("input_tokens") or usage_meta.get("prompt_tokens") or 0
                    output_tokens = usage_meta.get("output_tokens") or usage_meta.get("completion_tokens") or 0
                    total_tokens = usage_meta.get("total_tokens") or (input_tokens + output_tokens)
                    if input_tokens or output_tokens:
                        break
                gen_info = getattr(gen, "generation_info", None)
                if isinstance(gen_info, dict) and "usage_metadata" in gen_info:
                    um = gen_info["usage_metadata"]
                    if isinstance(um, dict):
                        input_tokens = um.get("input_tokens") or um.get("prompt_tokens") or 0
                        output_tokens = um.get("output_tokens") or um.get("completion_tokens") or 0
                        total_tokens = um.get("total_tokens") or (input_tokens + output_tokens)
                        if input_tokens or output_tokens:
                            break
            if input_tokens or output_tokens:
                break

    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens

    return {
        "input": int(input_tokens),
        "output": int(output_tokens),
        "total": int(total_tokens),
    }


def calculate_llm_cost_details(input_tokens: int, output_tokens: int) -> dict[str, float]:
    """Calculate deterministic USD cost details for LLM token usage using configured unit prices."""
    settings = get_settings()
    input_cost = (input_tokens * settings.llm_cost_per_1k_input_tokens) / 1000.0
    output_cost = (output_tokens * settings.llm_cost_per_1k_output_tokens) / 1000.0
    return {
        "input": input_cost,
        "output": output_cost,
        "total": input_cost + output_cost,
    }


class LangfuseCallbackHandler(CallbackHandler):
    """Langfuse v5 CallbackHandler extended with workflow trace metadata and tags."""

    def __init__(
        self,
        *,
        public_key: str | None = None,
        trace_context: dict | None = None,
        trace_name: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(public_key=public_key, trace_context=trace_context)
        self.trace_name = trace_name
        self.session_id = session_id
        self.user_id = user_id
        self.tags = tags or []
        self.metadata = metadata or {}

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if parent_run_id is None:
            metadata = dict(metadata) if metadata else {}
            if self.trace_name and "langfuse_trace_name" not in metadata:
                metadata["langfuse_trace_name"] = self.trace_name
            if self.session_id and "langfuse_session_id" not in metadata:
                metadata["langfuse_session_id"] = self.session_id
            if self.user_id and "langfuse_user_id" not in metadata:
                metadata["langfuse_user_id"] = self.user_id
            if self.metadata:
                for k, v in self.metadata.items():
                    metadata.setdefault(k, v)
            if self.tags:
                tags = list(set((tags or []) + self.tags))
        return super().on_chain_start(
            serialized,
            inputs,
            run_id=run_id,
            parent_run_id=parent_run_id,
            tags=tags,
            metadata=metadata,
            **kwargs,
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """Inject deterministic LLM unit token costs into Langfuse generation observation before completion."""
        try:
            generation = self._runs.get(run_id)
            if generation is not None:
                usage = extract_llm_token_counts(response)
                cost_details = calculate_llm_cost_details(usage["input"], usage["output"])
                update_kwargs: dict[str, Any] = {"cost_details": cost_details}
                if usage["total"] > 0:
                    update_kwargs["usage_details"] = usage
                if hasattr(generation, "update"):
                    generation.update(**update_kwargs)
        except Exception as exc:
            logger.debug("Failed to attach LLM cost details: %s", exc)

        return super().on_llm_end(
            response,
            run_id=run_id,
            parent_run_id=parent_run_id,
            **kwargs,
        )


def get_langfuse_client() -> Langfuse | None:
    global _langfuse_client
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    if _langfuse_client is None:
        try:
            _langfuse_client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                host=settings.langfuse_host,
            )
        except Exception as exc:
            logger.warning("Failed to initialize Langfuse client: %s", exc)
            return None
    return _langfuse_client


def get_langfuse_callback(
    workflow_name: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> CallbackHandler | None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    client = get_langfuse_client()
    if not client:
        return None

    try:
        combined_tags = [f"workflow:{workflow_name}", f"env:{settings.app_env}"]
        if tags:
            combined_tags.extend(tags)

        trace_name = f"rag:{workflow_name}"
        if not trace_id:
            ctx_tid = structlog.contextvars.get_contextvars().get("trace_id")
            if ctx_tid:
                trace_id = str(ctx_tid).replace("-", "").lower()
            else:
                trace_id = uuid.uuid4().hex
        else:
            trace_id = str(trace_id).replace("-", "").lower()
        trace_ctx = {"trace_id": trace_id}

        return LangfuseCallbackHandler(
            public_key=settings.langfuse_public_key,
            trace_context=trace_ctx,
            trace_name=trace_name,
            session_id=session_id,
            user_id=user_id,
            tags=list(set(combined_tags)),
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.warning("Failed to create Langfuse callback handler: %s", exc)
        return None


def extract_trace_id(handler: Any | None) -> str | None:
    """Safely extract the trace ID from a callback handler across Langfuse versions."""
    if handler is None:
        return None
    try:
        last_id = getattr(handler, "last_trace_id", None)
        if last_id:
            return str(last_id)

        ctx = getattr(handler, "_trace_context", None)
        if isinstance(ctx, dict) and ctx.get("trace_id"):
            return str(ctx["trace_id"])
        elif hasattr(ctx, "trace_id") and getattr(ctx, "trace_id"):
            return str(getattr(ctx, "trace_id"))

        if hasattr(handler, "get_trace_id") and callable(handler.get_trace_id):
            res = handler.get_trace_id()
            if res:
                return str(res)

        tid = getattr(handler, "trace_id", None)
        if tid:
            return str(tid)

        root_id = getattr(handler, "root_trace_id", None)
        if root_id:
            return str(root_id)
    except Exception as exc:
        logger.debug("Unable to extract trace_id from handler: %s", exc)
    return None


def flush_telemetry() -> None:
    """Flush pending Langfuse telemetry events on shutdown or lifecycle hook."""
    global _langfuse_client
    if _langfuse_client is not None:
        try:
            if hasattr(_langfuse_client, "flush") and callable(_langfuse_client.flush):
                _langfuse_client.flush()
        except Exception as exc:
            logger.warning("Error flushing Langfuse telemetry: %s", exc)


