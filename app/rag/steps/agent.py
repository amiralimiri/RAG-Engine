"""Autonomous multi-agent workflow step with dynamic handoff tool synthesis."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import structlog
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.rag.prompts import PromptStore, format_chat_history, get_prompt_store
from app.rag.steps.base import BaseStep, register_step
from app.rag.tools.base import aggregate_tool_evidence

logger = structlog.get_logger(__name__)

DEFAULT_AGENT_PROMPT_KEY = "rag:agent"
DEFAULT_MAX_ITERATIONS = 10
HANDOFF_TOOL_PREFIX = "transfer_to_"


class HandoffTransferInput(BaseModel):
    """Arguments accepted by a synthesized agent handoff tool."""

    reason: str = Field(
        description="Concise justification for transferring control to the target agent.",
    )


def create_handoff_tool(target_agent: str) -> StructuredTool:
    """Create a structured transfer tool that hands control to ``target_agent``.

    The generated tool follows the canonical ``transfer_to_<target_agent>`` naming
    convention and accepts a single ``reason`` argument so the transfer decision is
    recorded in the conversation transcript and execution trace.
    """
    target = target_agent.strip() if isinstance(target_agent, str) else ""
    if not target:
        raise ValueError("Handoff target agent must be a non-empty string.")

    tool_name = f"{HANDOFF_TOOL_PREFIX}{target}"

    def _transfer(reason: str) -> str:
        return f"Transferring control to agent '{target}'. Reason: {reason}"

    return StructuredTool.from_function(
        func=_transfer,
        name=tool_name,
        description=(
            f"Transfer control to the '{target}' specialist agent when their expertise "
            "is required. Provide a concise reason explaining why the handoff is necessary."
        ),
        args_schema=HandoffTransferInput,
    )


def _message_text(message: Any) -> str:
    """Extract plain text from a chat message or model response content payload."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content) if content is not None else ""


@register_step("agent")
class AgentStep(BaseStep):
    """Autonomous tool-calling agent step with scoped tools and dynamic handoffs.

    The step renders an injected prompt template, binds only its assigned domain tools
    plus any synthesized ``transfer_to_<agent>`` handoff tools, and executes a bounded
    tool-calling loop. When the model invokes a handoff tool, the transfer is recorded
    in the emitted messages/trace and ``active_agent`` is set to the target agent.
    """

    DEFAULT_OUTPUTS: dict[str, str] = {
        "answer": "answer",
        "messages": "messages",
        "active_agent": "active_agent",
        "citations": "citations",
        "retrieved_contexts": "retrieved_contexts",
        "source_used": "source_used",
        "trace": "trace",
    }

    def __init__(
        self,
        model: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        *,
        prompt_key: str = DEFAULT_AGENT_PROMPT_KEY,
        prompt_store: PromptStore | None = None,
        handoffs: Sequence[str] | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1.")

        super().__init__(
            name=name or "agent",
            inputs=inputs,
            outputs=outputs if outputs is not None else dict(self.DEFAULT_OUTPUTS),
        )
        self.model = model
        self.tools = list(tools or [])
        self.prompt_key = prompt_key
        self._prompt_store = prompt_store
        self.max_iterations = max_iterations

        # Preserve declaration order while de-duplicating repeated handoff targets.
        unique_handoffs = list(dict.fromkeys(target for target in (handoffs or []) if target))
        self.handoffs = unique_handoffs
        self._handoff_tools_by_name: dict[str, StructuredTool] = {
            tool.name: tool for tool in (create_handoff_tool(target) for target in unique_handoffs)
        }
        self.handoff_tools: list[StructuredTool] = list(self._handoff_tools_by_name.values())
        self._handoff_targets: dict[str, str] = {
            name: name.removeprefix(HANDOFF_TOOL_PREFIX) for name in self._handoff_tools_by_name
        }

    @property
    def prompt_store(self) -> PromptStore:
        """Resolve the injected prompt store, falling back to the global singleton lazily."""
        if self._prompt_store is not None:
            return self._prompt_store
        return get_prompt_store()

    @property
    def executable_tools(self) -> list[BaseTool]:
        """Return the strictly scoped tool set: domain tools plus handoff tools."""
        return [*self.tools, *self.handoff_tools]

    async def execute(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step=self.name)

        question = self.resolve_input(state, "question", default="") or ""
        current_query = self.resolve_input(state, "current_query") or question
        history = list(self.resolve_input(state, "messages", default=[]) or [])
        if not history and question:
            history = [HumanMessage(content=question)]

        rendered = self.prompt_store.render(
            self.prompt_key,
            config=config,
            question=question,
            current_query=current_query,
            chat_history=format_chat_history(history[:-1] if history else []),
        )
        call_config = cast(RunnableConfig, rendered.config)

        conversation: list[BaseMessage] = [SystemMessage(content=rendered.text), *history]
        new_messages: list[BaseMessage] = []
        trace: list[str] = []
        active_agent: str | None = None

        tools = self.executable_tools
        tool_map = {tool.name: tool for tool in tools}
        bound_model: Any = self.model.bind_tools(tools) if tools else self.model

        for iteration in range(1, self.max_iterations + 1):
            response = await bound_model.ainvoke(conversation, config=call_config)
            ai_message = response if isinstance(response, AIMessage) else AIMessage(content=_message_text(response))
            conversation.append(ai_message)
            new_messages.append(ai_message)

            tool_calls = list(ai_message.tool_calls or [])
            if not tool_calls:
                break

            handoff_target: str | None = None
            for tool_call in tool_calls:
                tool_name = str(tool_call.get("name") or "")
                tool_args = tool_call.get("args") or {}
                tool_call_id = str(tool_call.get("id") or f"call_{iteration}_{tool_name}")

                if handoff_target is not None:
                    # Every tool call must receive a ToolMessage for valid replay history.
                    skipped = ToolMessage(
                        content=(
                            f"Tool call skipped because control was transferred to agent '{handoff_target}'."
                        ),
                        name=tool_name,
                        tool_call_id=tool_call_id,
                    )
                    conversation.append(skipped)
                    new_messages.append(skipped)
                    trace.append(f"Agent '{self.name}' skipped tool '{tool_name}' after handoff")
                    continue

                target_agent = self._handoff_targets.get(tool_name)
                if target_agent is not None:
                    reason = str(tool_args.get("reason") or "").strip()
                    handoff_message = await self._invoke_handoff_tool(
                        tool_name, tool_call_id, tool_args, call_config
                    )
                    conversation.append(handoff_message)
                    new_messages.append(handoff_message)
                    active_agent = target_agent
                    handoff_target = target_agent
                    if getattr(handoff_message, "status", "success") == "error":
                        trace.append(
                            f"Agent '{self.name}' handoff tool '{tool_name}' failed; "
                            f"forcing transfer to '{target_agent}'"
                        )
                    else:
                        trace.append(f"Agent '{self.name}' → {tool_name}: {reason}")
                    continue

                tool = tool_map.get(tool_name)
                if tool is None:
                    blocked = ToolMessage(
                        content=f"Error: tool '{tool_name}' is not available to agent '{self.name}'.",
                        name=tool_name,
                        tool_call_id=tool_call_id,
                    )
                    conversation.append(blocked)
                    new_messages.append(blocked)
                    trace.append(f"Agent '{self.name}' blocked unavailable tool '{tool_name}'")
                    continue

                tool_message = await self._execute_tool(
                    tool, tool_name, tool_call_id, tool_args, call_config
                )
                conversation.append(tool_message)
                new_messages.append(tool_message)
                if getattr(tool_message, "status", "success") == "error":
                    trace.append(f"Agent '{self.name}' tool '{tool_name}' failed")
                else:
                    trace.append(f"Agent '{self.name}' executed tool '{tool_name}'")

            if handoff_target is not None:
                break
        else:
            trace.append(f"Agent '{self.name}' stopped after {self.max_iterations} iterations")

        if not trace:
            trace.append(f"Agent '{self.name}' completed")

        answer = self._final_answer(new_messages)
        evidence = aggregate_tool_evidence(new_messages)

        payload: dict[str, Any] = {
            "messages": new_messages,
            "citations": evidence.citations,
            "retrieved_contexts": evidence.retrieved_contexts,
            "source_used": evidence.source_used,
            "trace": trace,
        }
        if answer:
            payload["answer"] = answer
        if active_agent is not None:
            payload["active_agent"] = active_agent

        return self.apply_outputs(payload, state)

    async def _invoke_handoff_tool(
        self,
        tool_name: str,
        tool_call_id: str,
        tool_args: dict[str, Any],
        config: RunnableConfig,
    ) -> ToolMessage:
        """Execute the synthesized handoff tool and normalize its result to a ToolMessage."""
        handoff_tool = self._handoff_tools_by_name[tool_name]
        try:
            raw = await handoff_tool.ainvoke(
                {"name": tool_name, "args": tool_args, "id": tool_call_id, "type": "tool_call"},
                config=config,
            )
            if isinstance(raw, ToolMessage):
                return raw
            return ToolMessage(content=_message_text(raw), name=tool_name, tool_call_id=tool_call_id)
        except Exception as exc:  # noqa: BLE001 - handoff failure must not abort the graph
            logger.warning("agent_handoff_tool_failed", tool=tool_name, error=str(exc))
            return ToolMessage(
                content=f"Error executing handoff tool '{tool_name}': {exc}",
                name=tool_name,
                tool_call_id=tool_call_id,
                status="error",
            )

    async def _execute_tool(
        self,
        tool: BaseTool,
        tool_name: str,
        tool_call_id: str,
        tool_args: dict[str, Any],
        config: RunnableConfig | None,
    ) -> ToolMessage:
        """Execute a scoped domain tool, isolating failures into an error ToolMessage."""
        try:
            raw = await tool.ainvoke(
                {"name": tool_name, "args": tool_args, "id": tool_call_id, "type": "tool_call"},
                config=config,
            )
            if isinstance(raw, ToolMessage):
                return raw
            return ToolMessage(content=_message_text(raw), name=tool_name, tool_call_id=tool_call_id)
        except Exception as exc:  # noqa: BLE001 - per-tool isolation keeps the loop alive
            logger.warning("agent_tool_failed", tool=tool_name, error=str(exc))
            return ToolMessage(
                content=f"Error executing tool '{tool_name}': {exc}",
                name=tool_name,
                tool_call_id=tool_call_id,
                status="error",
            )

    @staticmethod
    def _final_answer(messages: Sequence[BaseMessage]) -> str:
        """Return the last non-empty assistant response, if any."""
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                text = _message_text(message).strip()
                if text:
                    return text
        return ""


__all__ = [
    "AgentStep",
    "HandoffTransferInput",
    "create_handoff_tool",
    "DEFAULT_AGENT_PROMPT_KEY",
    "DEFAULT_MAX_ITERATIONS",
    "HANDOFF_TOOL_PREFIX",
]
