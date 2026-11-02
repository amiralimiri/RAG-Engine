"""Autonomous ReAct agent workflow implementation with checkpointer memory."""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool

from app.rag.workflows.base import (
    EvalContext,
    WorkflowInfo,
    WorkflowInput,
    WorkflowOutput,
    parse_raw_attachments,
    parse_raw_messages,
)

DEFAULT_REACT_STEP_LABELS: dict[str, str] = {
    "agent": "Reasoning & planning response...",
    "model": "Reasoning & planning response...",
    "tools": "Executing selected tool...",
}

DEFAULT_REACT_GENERATION_NODES: set[str] = {"agent", "model"}

DEFAULT_REACT_PROMPT: str = (
    "You are an expert Enterprise IT Support Assistant. "
    "Consult the internal knowledge base with knowledge_base_search first. "
    "If internal documentation is insufficient, use web_search. "
    "Provide clear, accurate, and direct answers citing your sources."
)


def _is_tool_message(m: Any) -> bool:
    """Returns True if message is a ToolMessage instance or duck-typed tool message."""
    return isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool"


def _extract_tools_called(messages: list[Any]) -> list[str]:
    """Extracts unique tool names invoked across tool calls and tool output messages."""
    tools_called: list[str] = []
    for m in messages:
        tool_name = getattr(m, "name", None)
        if hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                if tc_name and tc_name not in tools_called:
                    tools_called.append(tc_name)
        if _is_tool_message(m):
            if tool_name and tool_name not in tools_called:
                tools_called.append(tool_name)
    return tools_called


def _resolve_source_used(
    tools_called: Sequence[str],
    explicit_source: str | None = None,
) -> str:
    """Consistently resolves evidence source used from executed tools and explicit source."""
    if explicit_source in ("error", "error_fallback"):
        return "error"
    if "knowledge_base_search" in tools_called and "web_search" in tools_called:
        return "react_agent"
    if "knowledge_base_search" in tools_called:
        return "vectorstore"
    if "web_search" in tools_called:
        return "web_search"
    if tools_called:
        return "react_agent"
    return explicit_source or "direct"


class ReActAgentWorkflow:
    """WorkflowDefinition implementation for an autonomous ReAct tool-calling agent with memory."""

    def __init__(
        self,
        id: str = "react_agent",
        name: str = "Autonomous ReAct Agent",
        description: str = (
            "Dynamic multi-turn tool-calling agent with conversational memory, "
            "capable of querying internal knowledge base and external search tools."
        ),
        llm: Any = None,
        tools: Sequence[BaseTool] | None = None,
        checkpointer: Any = None,
        step_labels: dict[str, str] | None = None,
        generation_nodes: set[str] | None = None,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.checkpointer = checkpointer
        self.system_prompt = system_prompt or DEFAULT_REACT_PROMPT
        default_metadata = {
            "classifier": "react_agent",
            "retrieval_strategy": "react_agent",
        }
        if metadata:
            default_metadata.update(metadata)
        self.info = WorkflowInfo(
            id=id,
            name=name,
            description=description,
            is_conversational=True,
            supports_streaming=True,
            step_labels=step_labels or dict(DEFAULT_REACT_STEP_LABELS),
            generation_nodes=generation_nodes or set(DEFAULT_REACT_GENERATION_NODES),
            metadata=default_metadata,
        )
        self._lock = threading.RLock()
        self._compiled_graph = None

    def _get_default_llm(self) -> Any:
        from app.rag.engine import get_chat_model

        return get_chat_model()

    def _get_default_tools(self) -> list[BaseTool]:
        from app.rag.tools import create_retriever_tool, create_web_search_tool

        return [create_retriever_tool(), create_web_search_tool()]

    def get_compiled_graph(self, variant_id: str | None = None) -> Any:
        with self._lock:
            if self._compiled_graph is None:
                active_llm = self.llm if self.llm is not None else self._get_default_llm()
                active_tools = self.tools if self.tools is not None else self._get_default_tools()
                self._compiled_graph = create_agent(
                    model=active_llm,
                    tools=active_tools,
                    checkpointer=self.checkpointer,
                    system_prompt=self.system_prompt,
                )
            return self._compiled_graph

    def to_initial_state(self, input: WorkflowInput | Any, trace_id: str | None = None) -> dict[str, Any]:
        raw_messages = getattr(input, "messages", None)
        question = getattr(input, "question", "")
        if self.checkpointer is not None:
            messages = [HumanMessage(content=question)] if question else []
        else:
            messages = parse_raw_messages(raw_messages, question)
        attachments = parse_raw_attachments(getattr(input, "attachments", None))

        return {
            "messages": messages,
            "question": question,
            "session_id": getattr(input, "session_id", None),
            "trace_id": trace_id or getattr(input, "trace_id", None),
            "attachments": attachments,
        }

    def to_workflow_output(self, final_state: dict[str, Any], trace_id: str | None = None) -> WorkflowOutput:
        messages = final_state.get("messages", [])
        last_ai = None
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                last_ai = m
                break

        if last_ai is not None:
            if isinstance(last_ai.content, str):
                answer = last_ai.content
            elif isinstance(last_ai.content, list):
                answer = "".join(
                    c.get("text", "") if isinstance(c, dict) else (getattr(c, "text", "") or str(c))
                    for c in last_ai.content
                )
            else:
                answer = str(last_ai.content)
        else:
            answer = final_state.get("answer") or ""

        citations: list[dict[str, Any]] = []
        retrieved_contexts: list[str] = list(final_state.get("retrieved_contexts", []))
        tools_called = _extract_tools_called(messages)

        for m in messages:
            if _is_tool_message(m):
                tool_name = getattr(m, "name", None)
                content_str = str(m.content)
                if content_str not in retrieved_contexts:
                    retrieved_contexts.append(content_str)

                # 1. Prefer structured artifact if attached to tool output
                artifact = getattr(m, "artifact", None)
                if isinstance(artifact, list) and artifact:
                    for item in artifact:
                        if isinstance(item, dict):
                            if not any(
                                (c.get("title") and c.get("title") == item.get("title"))
                                or (c.get("url") and c.get("url") == item.get("url"))
                                for c in citations
                            ):
                                citations.append(item)
                # 2. Fallback to regex scraping only if tool did not attach structured artifact
                elif tool_name == "knowledge_base_search":
                    for src in re.findall(r"\[Source:\s*([^\]]+)\]", content_str):
                        title = src.split("/")[-1].split("\\")[-1]
                        if not any(c.get("title") == title for c in citations):
                            citations.append({"title": title, "url": "", "type": "private_kb"})
                elif tool_name == "web_search":
                    for title, url in re.findall(r"Title:\s*([^\n]+)\nURL:\s*([^\n]+)", content_str):
                        if not any(c.get("url") == url for c in citations):
                            citations.append({"title": title or url, "url": url, "type": "web"})

        source_used = _resolve_source_used(
            tools_called=tools_called,
            explicit_source=final_state.get("source_used"),
        )

        question = final_state.get("question")
        if not question:
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    question = m.content if isinstance(m.content, str) else str(m.content)
                    break

        trace: list[str] = []
        if question:
            trace.append(f"Inquiry received: '{question}'")
        for t in tools_called:
            trace.append(f"ReAct Tool Executed: {t}")
        trace.append("Synthesized conversational response")

        resolved_tid = trace_id or final_state.get("trace_id")
        return WorkflowOutput(
            workflow=self.info.id,
            answer=answer,
            source_used=source_used,
            citations=citations or final_state.get("citations", []),
            trace=trace or final_state.get("trace", []),
            rewritten_query=question,
            trace_id=resolved_tid,
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        messages = final_state.get("messages", [])
        retrieved: list[str] = list(final_state.get("retrieved_contexts", []))
        if not retrieved:
            for m in messages:
                if _is_tool_message(m):
                    retrieved.append(str(m.content))
        tools_called = _extract_tools_called(messages)
        source = _resolve_source_used(tools_called=tools_called, explicit_source=final_state.get("source_used"))
        if source == "error":
            source = "direct"
        return EvalContext(source_used=source, retrieved_contexts=retrieved)

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        err_msg = (
            "I encountered an error while reasoning through your question. "
            "Please try again or contact the IT help desk."
        )
        return {
            "messages": [AIMessage(content=err_msg)],
            "answer": err_msg,
            "source_used": "error",
            "trace": [f"ReAct Agent Error: {str(exc)}"],
            "citations": [],
            "retrieved_contexts": [],
            "question": current_state.get("question", ""),
            "attachments": current_state.get("attachments", []),
        }


__all__ = [
    "DEFAULT_REACT_STEP_LABELS",
    "DEFAULT_REACT_GENERATION_NODES",
    "DEFAULT_REACT_PROMPT",
    "ReActAgentWorkflow",
]
