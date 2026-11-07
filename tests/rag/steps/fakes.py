"""Offline test doubles shared across concrete workflow step unit tests."""

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from pydantic import Field

from app.rag.classifiers import FakeClassifierAdapter
from app.rag.prompts import CANONICAL_PROMPTS, LocalPromptStore, RenderedPrompt


class FakeChatModel(BaseChatModel):
    """Deterministic chat model recording invocations without network access.

    ``response_messages`` can be supplied to script multi-turn tool-calling loops;
    when exhausted (or omitted) the model falls back to ``response_text``.
    """

    response_text: str = "fake response"
    response_messages: list[AIMessage] = Field(default_factory=list)
    ainvoke_calls: list[dict[str, Any]] = Field(default_factory=list)
    bind_calls: list[list[Any]] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        bound = list(tools)
        self.bind_calls.append(bound)
        self.bound_tools = bound
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.response_messages:
            message = self.response_messages.pop(0)
        else:
            message = AIMessage(content=self.response_text)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        self.ainvoke_calls.append({"input": input, "config": config, "stop": stop, "kwargs": kwargs})
        return await super().ainvoke(input, config=config, stop=stop, **kwargs)


class FakePromptStore(LocalPromptStore):
    """Local prompt store that records render calls for assertion."""

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        super().__init__(templates=templates if templates is not None else dict(CANONICAL_PROMPTS))
        self.render_calls: list[dict[str, Any]] = []

    def render(
        self,
        name: str,
        config: RunnableConfig | None = None,
        **variables: Any,
    ) -> RenderedPrompt:
        self.render_calls.append({"name": name, "config": config, "variables": variables})
        return super().render(name, config=config, **variables)


class FakeWorkflowClassifier(FakeClassifierAdapter):
    """Explicitly named offline test double for the WorkflowClassifier seam."""
