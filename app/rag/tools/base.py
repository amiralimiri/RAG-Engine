"""Custom BaseTool abstractions for RAG workflows and autonomous agents."""

import types
import typing
from collections.abc import Callable, Sequence
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.rag.state import Citation


class ToolEvidenceArtifact(BaseModel):
    """Canonical structured evidence payload returned alongside tool display text."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    citations: list[Citation] = Field(default_factory=list)
    retrieved_contexts: list[str] = Field(default_factory=list)
    source_used: str = "direct"
    raw_payload: Any = None


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
    if hasattr(item, "artifact") and getattr(item, "artifact") is not None:
        raw_art = getattr(item, "artifact")
        if isinstance(raw_art, ToolEvidenceArtifact):
            return raw_art
        if isinstance(raw_art, dict):
            try:
                return ToolEvidenceArtifact.model_validate(raw_art)
            except Exception:
                pass
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


def _normalize_config_annotations(fn: Any) -> None:
    """Ensure LangChain parameter injection detects config: RunnableConfig | None.

    LangChain's internal `_get_runnable_config_param` checks `type_ is RunnableConfig`.
    If the function uses `RunnableConfig | None` or `Optional[RunnableConfig]`,
    LangChain fails to inject config. Normalizing the annotation to `RunnableConfig`
    restores seamless parameter injection while allowing default `None` values.
    """
    if fn is None or not hasattr(fn, "__annotations__"):
        return
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = getattr(fn, "__annotations__", {})

    for name, hint in hints.items():
        origin = typing.get_origin(hint)
        if origin is typing.Union or (hasattr(types, "UnionType") and origin is types.UnionType):
            args = typing.get_args(hint)
            if RunnableConfig in args:
                fn.__annotations__[name] = RunnableConfig


def _is_tool_call(input_val: Any) -> bool:
    return (
        isinstance(input_val, dict)
        and "name" in input_val
        and "args" in input_val
        and ("id" in input_val or "tool_call_id" in input_val)
    )


def _to_tool_result(raw: Any) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, ToolMessage):
        content = str(raw.content)
        artifact = raw.artifact
    elif isinstance(raw, tuple) and len(raw) == 2:
        content, artifact = raw
        content = str(content)
    else:
        content = str(raw)
        artifact = None

    if isinstance(artifact, ToolEvidenceArtifact):
        typed_art = artifact
    elif isinstance(artifact, dict):
        raw_payload = artifact.get("raw_payload", artifact.get("documents"))
        citations = artifact.get("citations", [])
        retrieved_contexts = artifact.get("retrieved_contexts", [])
        source_used = artifact.get("source_used", "direct")
        typed_art = ToolEvidenceArtifact(
            citations=citations,
            retrieved_contexts=retrieved_contexts,
            source_used=source_used,
            raw_payload=raw_payload,
        )
    elif artifact is None:
        typed_art = ToolEvidenceArtifact()
    else:
        typed_art = ToolEvidenceArtifact(raw_payload=artifact)

    return ToolResult(content=content, artifact=typed_art)


class RAGTool(StructuredTool):
    """Deep tool abstraction for RAG workflows and autonomous agents.

    Provides explicit `.execute()` and async `.aexecute()` entrypoints returning
    strongly-typed `ToolResult` containers for direct workflow callers, while
    preserving native LangChain/LangGraph `ToolMessage` protocol execution.
    """

    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any] | None = None,
        coroutine: Callable[..., Any] | None = None,
        name: str | None = None,
        description: str | None = None,
        return_direct: bool = False,
        args_schema: Any = None,
        infer_schema: bool = True,
        response_format: Literal["content", "content_and_artifact"] = "content_and_artifact",
        parse_docstring: bool = False,
        error_on_invalid_docstring: bool = False,
        **kwargs: Any,
    ) -> RAGTool:
        if func is not None:
            _normalize_config_annotations(func)
        if coroutine is not None:
            _normalize_config_annotations(coroutine)
        return super().from_function(
            func=func,
            coroutine=coroutine,
            name=name,
            description=description,
            return_direct=return_direct,
            args_schema=args_schema,
            infer_schema=infer_schema,
            response_format=response_format,
            parse_docstring=parse_docstring,
            error_on_invalid_docstring=error_on_invalid_docstring,
            **kwargs,
        )  # type: ignore

    def _normalize_input(self, input_args: Any, kwargs: dict[str, Any]) -> dict[str, Any] | str:
        if isinstance(input_args, BaseModel):
            data = input_args.model_dump()
            return {**data, **kwargs}
        if isinstance(input_args, str):
            schema = self.args_schema
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                fields = list(schema.model_fields.keys())
                if len(fields) == 1:
                    return {fields[0]: input_args, **kwargs}
            return input_args
        if isinstance(input_args, dict):
            return {**input_args, **kwargs}
        if kwargs:
            return kwargs
        return {}

    def execute(
        self,
        input_args: Any = None,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Synchronously execute tool and wrap display content and evidence into ToolResult."""
        normalized = self._normalize_input(input_args, kwargs)
        raw = self.run(
            normalized,
            callbacks=config.get("callbacks") if config else None,
            tags=config.get("tags") if config else None,
            metadata=config.get("metadata") if config else None,
            run_name=config.get("run_name") if config else None,
            config=config,
            tool_call_id=f"exec_{self.name}",
            **kwargs,
        )
        return _to_tool_result(raw)

    async def aexecute(
        self,
        input_args: Any = None,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Asynchronously execute tool and wrap display content and evidence into ToolResult."""
        normalized = self._normalize_input(input_args, kwargs)
        raw = await self.arun(
            normalized,
            callbacks=config.get("callbacks") if config else None,
            tags=config.get("tags") if config else None,
            metadata=config.get("metadata") if config else None,
            run_name=config.get("run_name") if config else None,
            config=config,
            tool_call_id=f"exec_{self.name}",
            **kwargs,
        )
        return _to_tool_result(raw)

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke the tool.

        When invoked with a ToolCall dictionary (e.g. from LangGraph ToolNode),
        executes standard LangChain protocol producing a ToolMessage.
        When invoked directly with input args, executes cleanly returning ToolResult.
        """
        if _is_tool_call(input) or "tool_call_id" in kwargs:
            return super().invoke(input, config=config, **kwargs)
        return self.execute(input, config=config, **kwargs)

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        """Asynchronously invoke the tool.

        When invoked with a ToolCall dictionary (e.g. from LangGraph ToolNode),
        executes standard LangChain protocol producing a ToolMessage.
        When invoked directly with input args, executes cleanly returning ToolResult.
        """
        if _is_tool_call(input) or "tool_call_id" in kwargs:
            return await super().ainvoke(input, config=config, **kwargs)
        return await self.aexecute(input, config=config, **kwargs)


def rag_tool(
    func: Callable[..., Any] | None = None,
    coroutine: Callable[..., Any] | None = None,
    name: str = "",
    description: str = "",
    args_schema: type[BaseModel] | None = None,
    **kwargs: Any,
) -> RAGTool:
    """Factory creating a RAGTool configured with response_format='content_and_artifact'."""
    return RAGTool.from_function(
        func=func,
        coroutine=coroutine,
        name=name,
        description=description,
        args_schema=args_schema,
        response_format="content_and_artifact",
        **kwargs,
    )


class _FakeDefaultSchema(BaseModel):
    query: str = ""


class FakeRAGTool(RAGTool):
    """In-memory test double for RAGTool supporting deterministic testing."""

    name: str = "fake_rag_tool"
    description: str = "In-memory test double for RAGTool."
    args_schema: Any = _FakeDefaultSchema
    content: str = ""
    artifact: ToolEvidenceArtifact = Field(default_factory=ToolEvidenceArtifact)
    execute_calls: list[dict[str, Any]] = Field(default_factory=list)
    aexecute_calls: list[dict[str, Any]] = Field(default_factory=list)
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        _normalize_config_annotations(self._run)
        _normalize_config_annotations(self._arun)
        if self.func is None:
            self.func = self._run
        if self.coroutine is None:
            self.coroutine = self._arun

    def _run(
        self,
        *args: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> tuple[str, ToolEvidenceArtifact]:
        call_input = kwargs if kwargs and not args else (args[0] if len(args) == 1 else args)
        self.execute_calls.append({"input": call_input, "config": config})
        return self.content, self.artifact

    async def _arun(
        self,
        *args: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> tuple[str, ToolEvidenceArtifact]:
        call_input = kwargs if kwargs and not args else (args[0] if len(args) == 1 else args)
        self.aexecute_calls.append({"input": call_input, "config": config})
        return self.content, self.artifact

    def execute(
        self,
        input_args: Any = None,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        self.execute_calls.append({"input": input_args, "config": config, **kwargs})
        return ToolResult(content=self.content, artifact=self.artifact)

    async def aexecute(
        self,
        input_args: Any = None,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        self.aexecute_calls.append({"input": input_args, "config": config, **kwargs})
        return ToolResult(content=self.content, artifact=self.artifact)


__all__ = [
    "ToolEvidenceArtifact",
    "ToolResult",
    "AggregatedEvidence",
    "aggregate_tool_evidence",
    "RAGTool",
    "rag_tool",
    "FakeRAGTool",
]
