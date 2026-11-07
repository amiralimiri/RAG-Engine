"""Retrieval workflow step wrapping RAGTool execution and evidence aggregation."""

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from app.rag.steps.base import BaseStep, register_step
from app.rag.tools.base import RAGTool, aggregate_tool_evidence

logger = structlog.get_logger(__name__)


@register_step("tool_retrieval")
class ToolRetrievalStep(BaseStep):
    """Execute an injected RAGTool and emit normalized evidence into workflow state.

    The step reads its search query from ``current_query`` (falling back to ``question``),
    invokes the injected tool asynchronously, aggregates the returned evidence artifact,
    and writes ``retrieved_contexts``, ``citations``, ``source_used``, and ``trace``.

    Optional ``documents`` and ``content`` logical outputs are only emitted when they are
    explicitly mapped (for KB document payloads or raw web/search display content).
    """

    DEFAULT_OUTPUTS: dict[str, str] = {
        "retrieved_contexts": "retrieved_contexts",
        "citations": "citations",
        "source_used": "source_used",
        "trace": "trace",
    }

    def __init__(
        self,
        tool: RAGTool,
        *,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
        trace_template: str = "Tool retrieval → {count} results",
        source_used: str | None = None,
        emit_source_used: bool = True,
    ) -> None:
        super().__init__(
            name=name or tool.name,
            inputs=inputs,
            outputs=outputs if outputs is not None else dict(self.DEFAULT_OUTPUTS),
        )
        self.tool = tool
        self.trace_template = trace_template
        self.source_used = source_used
        self.emit_source_used = emit_source_used

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step=self.name)

        # A logical "query" input is only honored when explicitly mapped; otherwise
        # canonical current_query/question precedence is used to avoid stale top-level keys.
        if "query" in self.inputs:
            query = self.resolve_input(state, "query")
        else:
            query = self.resolve_input(state, "current_query") or self.resolve_input(state, "question")
        if not query:
            query = ""
        if not isinstance(query, str):
            query = str(query)

        result = await self.tool.aexecute({"query": query}, config=config)
        artifact = result.artifact
        evidence = aggregate_tool_evidence([artifact])

        contexts = evidence.retrieved_contexts or ([result.content] if result.content else [])
        documents = artifact.raw_payload if isinstance(artifact.raw_payload, list) else []
        resolved_source = self.source_used or evidence.source_used
        trace = self.trace_template.format(
            count=len(documents),
            content=result.content,
            query=query,
            source=resolved_source,
        )

        payload: dict[str, Any] = {
            "retrieved_contexts": contexts,
            "citations": evidence.citations,
            "trace": [trace],
        }
        if self.emit_source_used:
            payload["source_used"] = resolved_source
        if "documents" in self.outputs:
            payload["documents"] = documents
        if "content" in self.outputs:
            payload["content"] = result.content

        return self.apply_outputs(payload, state)


__all__ = ["ToolRetrievalStep"]
