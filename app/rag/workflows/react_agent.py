"""Autonomous ReAct agent workflow implementation with checkpointer memory."""

from __future__ import annotations

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

from app.rag.tools.base import AggregatedEvidence, aggregate_tool_evidence
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


def _resolve_evidence_source(evidence: AggregatedEvidence, explicit_source: str | None = None) -> str:
    """Consistently resolves evidence source used from aggregated tool evidence and explicit source."""
    if explicit_source in ("error", "error_fallback"):
        return "error"
    if evidence.source_used != "direct":
        return evidence.source_used
    return explicit_source or "direct"


def _extract_contexts(
    evidence: AggregatedEvidence,
    final_state: dict[str, Any],
    messages: Sequence[Any],
) -> list[str]:
    """Collects and deduplicates retrieved contexts from aggregated evidence, state, and tool messages."""
    retrieved: list[str] = list(final_state.get("retrieved_contexts") or [])
    for ctx in evidence.retrieved_contexts:
        if ctx not in retrieved:
            retrieved.append(ctx)
    if not retrieved:
        for m in messages:
            if _is_tool_message(m):
                c_str = str(m.content)
                if c_str not in retrieved:
                    retrieved.append(c_str)
    return retrieved


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

        evidence = aggregate_tool_evidence(messages)
        source_used = _resolve_evidence_source(evidence, final_state.get("source_used"))

        citations = list(evidence.citations)
        if not citations and final_state.get("citations"):
            citations = list(final_state.get("citations") or [])

        retrieved_contexts = _extract_contexts(evidence, final_state, messages)

        question = final_state.get("question")
        if not question:
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    question = m.content if isinstance(m.content, str) else str(m.content)
                    break

        trace: list[str] = []
        if question:
            trace.append(f"Inquiry received: '{question}'")
        for m in messages:
            if _is_tool_message(m):
                tool_name = getattr(m, "name", None)
                if tool_name and f"ReAct Tool Executed: {tool_name}" not in trace:
                    trace.append(f"ReAct Tool Executed: {tool_name}")
            elif hasattr(m, "tool_calls") and m.tool_calls:
                for tc in m.tool_calls:
                    tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if tc_name and f"ReAct Tool Executed: {tc_name}" not in trace:
                        trace.append(f"ReAct Tool Executed: {tc_name}")
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
            retrieved_contexts=retrieved_contexts,
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        messages = final_state.get("messages", [])
        evidence = aggregate_tool_evidence(messages)
        retrieved = _extract_contexts(evidence, final_state, messages)

        source = _resolve_evidence_source(evidence, final_state.get("source_used"))
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
