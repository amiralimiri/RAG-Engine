"""Custom BaseTool abstractions for RAG workflows and autonomous agents."""

from contextvars import ContextVar
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

_UNSET = object()
_TOOL_INVOKE_CONFIG: ContextVar[Any] = ContextVar("_TOOL_INVOKE_CONFIG", default=_UNSET)


def get_invoke_config() -> Any:
    """Retrieve the original config passed to tool.invoke() or tool.ainvoke()."""
    return _TOOL_INVOKE_CONFIG.get()


class DualOutputStructuredTool(StructuredTool):
    """StructuredTool preserving (content, artifact) tuple when invoked directly without tool_call_id."""

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        token = _TOOL_INVOKE_CONFIG.set(config)
        try:
            return super().invoke(input, config=config, **kwargs)
        finally:
            _TOOL_INVOKE_CONFIG.reset(token)

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        token = _TOOL_INVOKE_CONFIG.set(config)
        try:
            return await super().ainvoke(input, config=config, **kwargs)
        finally:
            _TOOL_INVOKE_CONFIG.reset(token)

    def run(
        self,
        tool_input: str | dict[str, Any],
        *args: Any,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        is_direct = tool_call_id is None
        call_id = tool_call_id if not is_direct else "__direct__"
        res = super().run(tool_input, *args, tool_call_id=call_id, **kwargs)
        if is_direct and self.response_format == "content_and_artifact" and isinstance(res, ToolMessage):
            return (res.content, res.artifact)
        return res

    async def arun(
        self,
        tool_input: str | dict[str, Any],
        *args: Any,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        is_direct = tool_call_id is None
        call_id = tool_call_id if not is_direct else "__direct__"
        res = await super().arun(tool_input, *args, tool_call_id=call_id, **kwargs)
        if is_direct and self.response_format == "content_and_artifact" and isinstance(res, ToolMessage):
            return (res.content, res.artifact)
        return res


__all__ = [
    "_UNSET",
    "get_invoke_config",
    "DualOutputStructuredTool",
]
