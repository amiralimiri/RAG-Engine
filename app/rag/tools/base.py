"""Custom BaseTool abstractions for RAG workflows and autonomous agents."""

from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

_UNSET = object()
_TOOL_INVOKE_CONFIG: ContextVar[Any] = ContextVar("_TOOL_INVOKE_CONFIG", default=_UNSET)


def get_invoke_config() -> Any:
    """Retrieve the original config passed to tool.invoke() or tool.ainvoke()."""
    return _TOOL_INVOKE_CONFIG.get()


class Citation(BaseModel):
    """Strongly-typed reference citation attributed to retrieved evidence."""

    model_config = ConfigDict(frozen=True)

    title: str
    url: str = ""
    type: str = "general"


class ToolEvidenceArtifact(BaseModel):
    """Canonical structured evidence payload returned alongside tool display text."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    citations: list[Citation] = Field(default_factory=list)
    retrieved_contexts: list[str] = Field(default_factory=list)
    source_used: str = "direct"
    raw_payload: Any = None

    def __getitem__(self, key: str) -> Any:
        if key not in type(self).model_fields:
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        if key not in type(self).model_fields:
            return default
        return getattr(self, key)


class ToolResult(BaseModel):
    """Execution result container for direct workflow callers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str
    artifact: ToolEvidenceArtifact

    def __iter__(self) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        yield self.content
        yield self.artifact


class AggregatedEvidence(BaseModel):
    """Immutable domain container holding deduplicated citations and chronological contexts."""

    model_config = ConfigDict(frozen=True)

    citations: list[Citation] = Field(default_factory=list)
    retrieved_contexts: list[str] = Field(default_factory=list)
    source_used: str = "direct"


def _extract_artifact(item: Any) -> ToolEvidenceArtifact | None:
    if isinstance(item, ToolEvidenceArtifact):
        return item
    if hasattr(item, "artifact"):
        raw_art = getattr(item, "artifact")
        if isinstance(raw_art, ToolEvidenceArtifact):
            return raw_art
    return None


def aggregate_tool_evidence(messages_or_artifacts: Sequence[Any]) -> AggregatedEvidence:
    """Polymorphically aggregates and deduplicates citations and contexts across tool executions.

    - Extracts `ToolEvidenceArtifact` instances directly or from `ToolMessage.artifact`.
    - Deduplicates citations by unique `(title, url)`.
    - Preserves chronological order of `retrieved_contexts`.
    - Dynamically resolves `source_used`:
        * `"direct"` when empty.
        * Single tool source when uniform across invoked tools.
        * `"react_agent"` when multiple distinct tool sources were invoked.
    """
    artifacts: list[ToolEvidenceArtifact] = []
    for item in messages_or_artifacts:
        extracted = _extract_artifact(item)
        if extracted is not None:
            artifacts.append(extracted)

    if not artifacts:
        return AggregatedEvidence(citations=[], retrieved_contexts=[], source_used="direct")

    seen_citations: set[tuple[str, str]] = set()
    unique_citations: list[Citation] = []
    ordered_contexts: list[str] = []

    for art in artifacts:
        # Collect citations deduplicated by (title, url)
        for c in art.citations:
            key = (c.title, c.url)
            if key not in seen_citations:
                seen_citations.add(key)
                unique_citations.append(c)

        # Preserve chronological order of retrieved_contexts
        ordered_contexts.extend(art.retrieved_contexts)

    # Dynamic source resolution
    distinct_sources = {art.source_used for art in artifacts}
    if len(distinct_sources) == 0:
        resolved_source = "direct"
    elif len(distinct_sources) == 1:
        resolved_source = next(iter(distinct_sources))
    else:
        resolved_source = "react_agent"

    return AggregatedEvidence(
        citations=unique_citations,
        retrieved_contexts=ordered_contexts,
        source_used=resolved_source,
    )


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
    "Citation",
    "ToolEvidenceArtifact",
    "ToolResult",
    "AggregatedEvidence",
    "aggregate_tool_evidence",
]
