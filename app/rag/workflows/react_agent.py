"""Autonomous ReAct agent workflow implementation with checkpointer memory."""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING, Any, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo

if TYPE_CHECKING:
    from app.api.schemas import ChatRequest, ChatResponse

DEFAULT_REACT_STEP_LABELS: dict[str, str] = {
    "agent": "Reasoning & planning response...",
    "tools": "Executing selected tool...",
}

DEFAULT_REACT_GENERATION_NODES: set[str] = {"agent"}

DEFAULT_REACT_PROMPT: str = (
    "You are an expert Enterprise IT Support Assistant. "
    "Consult the internal knowledge base with knowledge_base_search first. "
    "If internal documentation is insufficient, use web_search. "
    "Provide clear, accurate, and direct answers citing your sources."
)


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
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.checkpointer = checkpointer
        self.system_prompt = system_prompt or DEFAULT_REACT_PROMPT
        self.info = WorkflowInfo(
            id=id,
            name=name,
            description=description,
            is_conversational=True,
            supports_streaming=True,
            step_labels=step_labels or dict(DEFAULT_REACT_STEP_LABELS),
            generation_nodes=generation_nodes or set(DEFAULT_REACT_GENERATION_NODES),
        )
        self._lock = threading.RLock()
        self._compiled_graph = None

    def _get_default_llm(self) -> Any:
        from app.rag.engine import llm
        return llm()

    def _get_default_tools(self) -> list[BaseTool]:
        from app.rag.tools import create_retriever_tool, create_web_search_tool
        return [create_retriever_tool(), create_web_search_tool()]

    def get_compiled_graph(self) -> Any:
        with self._lock:
            if self._compiled_graph is None:
                active_llm = self.llm if self.llm is not None else self._get_default_llm()
                active_tools = self.tools if self.tools is not None else self._get_default_tools()
                active_checkpointer = (
                    self.checkpointer if self.checkpointer is not None else MemorySaver()
                )
                self._compiled_graph = create_react_agent(
                    model=active_llm,
                    tools=active_tools,
                    checkpointer=active_checkpointer,
                    prompt=self.system_prompt,
                )
            return self._compiled_graph

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        messages: list[BaseMessage] = []
        if request.messages:
            for m in request.messages:
                role = (m.role or "").lower()
                if role in ("user", "human"):
                    messages.append(HumanMessage(content=m.content))
                elif role in ("assistant", "ai"):
                    messages.append(AIMessage(content=m.content))
                elif role == "system":
                    messages.append(SystemMessage(content=m.content))
                else:
                    messages.append(HumanMessage(content=m.content))

        # Append incoming question as latest HumanMessage if not already ending with it
        if not messages or getattr(messages[-1], "content", None) != request.question:
            messages.append(HumanMessage(content=request.question))

        return {
            "messages": messages,
            "question": request.question,
            "session_id": request.session_id,
            "trace_id": trace_id or request.trace_id,
        }

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        from app.api.schemas import ChatResponse

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
        tools_called: list[str] = []

        for m in messages:
            tool_name = getattr(m, "name", None)
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in m.tool_calls:
                    tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if tc_name and tc_name not in tools_called:
                        tools_called.append(tc_name)

            if isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool":
                if tool_name and tool_name not in tools_called:
                    tools_called.append(tool_name)
                content_str = str(m.content)
                if content_str not in retrieved_contexts:
                    retrieved_contexts.append(content_str)

                if tool_name == "knowledge_base_search":
                    for src in re.findall(r"\[Source:\s*([^\]]+)\]", content_str):
                        title = src.split("/")[-1].split("\\")[-1]
                        if not any(c.get("title") == title for c in citations):
                            citations.append({"title": title, "url": "", "type": "private_kb"})
                elif tool_name == "web_search":
                    for title, url in re.findall(r"Title:\s*([^\n]+)\nURL:\s*([^\n]+)", content_str):
                        if not any(c.get("url") == url for c in citations):
                            citations.append({"title": title or url, "url": url, "type": "web"})

        if "knowledge_base_search" in tools_called and "web_search" in tools_called:
            source_used = "hybrid"
        elif "knowledge_base_search" in tools_called:
            source_used = "vectorstore"
        elif "web_search" in tools_called:
            source_used = "web_search"
        elif tools_called:
            source_used = "tool_calling"
        else:
            source_used = final_state.get("source_used") or "direct"

        trace: list[str] = []
        q = final_state.get("question") or ""
        if q:
            trace.append(f"Inquiry received: '{q}'")
        for t in tools_called:
            trace.append(f"ReAct Tool Executed: {t}")
        trace.append("Synthesized conversational response")

        resolved_tid = trace_id or final_state.get("trace_id")
        return ChatResponse(
            workflow=self.info.id,
            answer=answer,
            source_used=source_used,
            citations=citations or final_state.get("citations", []),
            trace=trace or final_state.get("trace", []),
            rewritten_query=final_state.get("question"),
            trace_id=resolved_tid,
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        retrieved: list[str] = list(final_state.get("retrieved_contexts", []))
        if not retrieved:
            for m in final_state.get("messages", []):
                if isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool":
                    retrieved.append(str(m.content))
        source = final_state.get("source_used") or ("react_agent" if retrieved else "direct")
        return EvalContext(source_used=source, retrieved_contexts=retrieved)

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        err_msg = (
            "I encountered an error while reasoning through your question. "
            "Please try again or contact the IT help desk."
        )
        return {
            "messages": [AIMessage(content=err_msg)],
            "answer": err_msg,
            "source_used": "error_fallback",
            "trace": [f"ReAct Agent Error: {str(exc)}"],
            "citations": [],
            "retrieved_contexts": [],
            "question": current_state.get("question", ""),
        }


__all__ = [
    "DEFAULT_REACT_STEP_LABELS",
    "DEFAULT_REACT_GENERATION_NODES",
    "DEFAULT_REACT_PROMPT",
    "ReActAgentWorkflow",
]
