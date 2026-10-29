"""Custom BaseTool abstractions for RAG workflows and autonomous agents."""

from typing import Any
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool


class DualOutputStructuredTool(StructuredTool):
    """StructuredTool preserving (content, artifact) tuple when invoked directly without tool_call_id."""

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
    "DualOutputStructuredTool",
]
