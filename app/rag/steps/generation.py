"""Prompt generation and query reformulation workflow steps."""

from __future__ import annotations

from typing import Any, cast

import structlog
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from app.rag.prompts import PromptStore, RenderedPrompt, get_prompt_store
from app.rag.prompts.formatting import format_chat_history
from app.rag.steps.base import BaseStep, register_step
from app.rag.tools.retriever import format_documents

logger = structlog.get_logger(__name__)

GENERATION_MODES: dict[str, tuple[str, str, str, str]] = {
    "kb": ("rag:generate_kb", "vectorstore", "Answer generation → PRIVATE KB", "generate_from_kb"),
    "web": ("rag:generate_web", "web_search", "Answer generation → WEB SEARCH", "generate_from_web"),
    "direct": ("rag:direct_answer", "direct", "Direct response → no retrieval", "direct_answer"),
}

REFORMULATION_MODES: dict[str, tuple[str, str]] = {
    "condense": ("rag:condense_query", "condense_query"),
    "rewrite": ("rag:rewrite_query", "rewrite_query"),
}


class _ModelPromptStep(BaseStep):
    """Shared base for steps that render prompts and invoke a chat model."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        prompt_store: PromptStore | None = None,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        super().__init__(name=name, inputs=inputs, outputs=outputs)
        self.model = model
        self._prompt_store = prompt_store

    @property
    def prompt_store(self) -> PromptStore:
        """Resolve the injected prompt store, falling back to the global singleton lazily."""
        if self._prompt_store is not None:
            return self._prompt_store
        return get_prompt_store()

    async def _invoke(self, rendered: RenderedPrompt) -> Any:
        response = await self.model.ainvoke(rendered.text, config=cast(RunnableConfig, rendered.config))
        return response.content if hasattr(response, "content") else str(response)


@register_step("prompt_generate")
class PromptGenerationStep(_ModelPromptStep):
    """Synthesize answers from private KB, web evidence, or direct conversation prompts."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        mode: str = "kb",
        prompt_store: PromptStore | None = None,
        prompt_key: str | None = None,
        source_used: str | None = None,
        trace: str | None = None,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        if mode not in GENERATION_MODES:
            raise ValueError(f"Unsupported generation mode '{mode}'. Expected one of {sorted(GENERATION_MODES)}.")

        default_prompt, default_source, default_trace, default_name = GENERATION_MODES[mode]
        self.mode = mode
        self.prompt_key = prompt_key or default_prompt
        self.source_used = source_used or default_source
        self.trace = trace or default_trace

        super().__init__(
            model,
            prompt_store=prompt_store,
            name=name or default_name,
            inputs=inputs,
            outputs=outputs
            if outputs is not None
            else {
                "answer": "answer",
                "source_used": "source_used",
                "retrieved_contexts": "retrieved_contexts",
                "citations": "citations",
                "trace": "trace",
            },
        )

    async def execute(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step=self.name)

        question = self.resolve_input(state, "question", default="") or ""
        current_query = self.resolve_input(state, "current_query") or question
        messages = self.resolve_input(state, "messages", default=[]) or []
        raw_history = messages[:-1] if messages else []
        history = format_chat_history(raw_history)

        compile_kwargs: dict[str, Any] = {"question": question, "current_query": current_query}
        if self.mode == "kb":
            kb_docs = self.resolve_input(state, "kb_docs", default=[]) or []
            compile_kwargs["context"] = format_documents(kb_docs)
        elif self.mode == "web":
            compile_kwargs["web_results"] = self.resolve_input(state, "web_results", default="") or ""

        if history:
            compile_kwargs["chat_history"] = history
            compile_kwargs["history"] = history

        rendered = self.prompt_store.render(self.prompt_key, config=config, **compile_kwargs)
        if history and history not in rendered.text:
            rendered = rendered.with_prefix(f"Chat History:\n{history}\n\n")

        answer = await self._invoke(rendered)

        payload: dict[str, Any] = {
            "answer": answer,
            "source_used": self.source_used,
            "trace": [self.trace],
        }
        if self.mode == "direct":
            payload["retrieved_contexts"] = []
            payload["citations"] = []
        else:
            payload["retrieved_contexts"] = self.resolve_input(state, "retrieved_contexts", default=[]) or []
            payload["citations"] = self.resolve_input(state, "citations", default=[]) or []

        return self.apply_outputs(payload, state)


@register_step("query_reformulate")
class QueryReformulationStep(_ModelPromptStep):
    """Condense conversational follow-ups or rewrite insufficient retrieval queries."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        mode: str = "condense",
        prompt_store: PromptStore | None = None,
        prompt_key: str | None = None,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        if mode not in REFORMULATION_MODES:
            raise ValueError(f"Unsupported reformulation mode '{mode}'. Expected one of {sorted(REFORMULATION_MODES)}.")

        default_prompt, default_name = REFORMULATION_MODES[mode]
        self.mode = mode
        self.prompt_key = prompt_key or default_prompt

        default_outputs = {"current_query": "current_query", "trace": "trace"}
        if mode == "rewrite":
            default_outputs["retry_count"] = "retry_count"

        super().__init__(
            model,
            prompt_store=prompt_store,
            name=name or default_name,
            inputs=inputs,
            outputs=outputs if outputs is not None else default_outputs,
        )

    async def execute(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step=self.name)

        if self.mode == "condense":
            return await self._condense(state, config)
        return await self._rewrite(state, config)

    async def _condense(self, state: dict[str, Any], config: RunnableConfig | None) -> dict[str, Any]:
        question = self.resolve_input(state, "question", default="") or ""
        messages = self.resolve_input(state, "messages", default=[]) or []
        raw_history = messages[:-1] if messages else []
        history = format_chat_history(raw_history)

        rendered = self.prompt_store.render(
            self.prompt_key,
            config=config,
            question=question,
            chat_history=history,
            history=history,
        )
        raw_query = await self._invoke(rendered)
        standalone_query = (raw_query if isinstance(raw_query, str) else str(raw_query)).strip()
        if not standalone_query:
            standalone_query = question

        return self.apply_outputs(
            {
                "current_query": standalone_query,
                "trace": [f"Query contextualized → '{standalone_query}'"],
            },
            state,
        )

    async def _rewrite(self, state: dict[str, Any], config: RunnableConfig | None) -> dict[str, Any]:
        question = self.resolve_input(state, "question", default="") or ""
        current_query = self.resolve_input(state, "current_query") or question

        rendered = self.prompt_store.render(
            self.prompt_key,
            config=config,
            question=question,
            current_query=current_query,
        )
        raw_rewritten = await self._invoke(rendered)
        rewritten = (raw_rewritten if isinstance(raw_rewritten, str) else str(raw_rewritten)).strip()
        retry_count = self.resolve_input(state, "retry_count", default=0) or 0

        return self.apply_outputs(
            {
                "current_query": rewritten,
                "retry_count": retry_count + 1,
                "trace": [f"Query rewrite → {rewritten}"],
            },
            state,
        )


__all__ = [
    "PromptGenerationStep",
    "QueryReformulationStep",
    "format_chat_history",
    "GENERATION_MODES",
    "REFORMULATION_MODES",
]
