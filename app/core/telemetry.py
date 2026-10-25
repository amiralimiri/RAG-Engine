import logging
from typing import Any, Optional

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_langfuse_client: Optional[Langfuse] = None


class LangfuseCallbackHandler(CallbackHandler):
    """Langfuse v5 CallbackHandler extended with workflow trace metadata and tags."""

    def __init__(
        self,
        *,
        public_key: Optional[str] = None,
        trace_context: Optional[dict] = None,
        trace_name: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        super().__init__(public_key=public_key, trace_context=trace_context)
        self.trace_name = trace_name
        self.session_id = session_id
        self.user_id = user_id
        self.tags = tags or []
        self.metadata = metadata or {}

    def on_chain_start(
        self,
        serialized: Optional[dict[str, Any]],
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
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


def get_langfuse_client() -> Optional[Langfuse]:
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
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Optional[CallbackHandler]:
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
        trace_ctx = {"trace_id": trace_id} if trace_id else None

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


def extract_trace_id(handler: Optional[Any]) -> Optional[str]:
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


