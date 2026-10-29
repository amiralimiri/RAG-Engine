"""Parameterized Self-Corrective RAG workflow implementation."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool

from app.rag.classifiers import WorkflowClassifier
from app.rag.engine import create_agentic_rag_graph
from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo

if TYPE_CHECKING:
    from app.api.schemas import ChatRequest, ChatResponse

DEFAULT_STEP_LABELS: dict[str, str] = {
    "route_question": "Analyzing inquiry & routing...",
    "retrieve_kb": "Searching internal knowledge base...",
    "grade_kb": "Evaluating evidence sufficiency...",
    "search_web": "Consulting external technical documentation...",
    "grade_web": "Verifying web source credibility...",
    "rewrite_query": "Refining search terms...",
    "generate_from_kb": "Synthesizing answer from knowledge base...",
    "generate_from_web": "Drafting answer from external documentation...",
    "direct_answer": "Formulating direct response...",
    "insufficient": "Assessing fallback options...",
}

DEFAULT_GENERATION_NODES: set[str] = {
    "generate_from_kb",
    "generate_from_web",
    "direct_answer",
}


class SelfCorrectiveRagWorkflow:
    """WorkflowDefinition implementation for the 10-node self-corrective RAG DAG."""

    def __init__(
        self,
        id: str,
        name: str,
        classifier: WorkflowClassifier,
        retriever: Any = None,
        *,
        kb_tool: BaseTool | None = None,
        web_tool: BaseTool | None = None,
        description: str | None = None,
        step_labels: dict[str, str] | None = None,
        generation_nodes: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.classifier = classifier
        self.retriever = retriever
        self.kb_tool = kb_tool
        self.web_tool = web_tool
        desc = description or f"Self-corrective RAG workflow powered by {classifier.__class__.__name__}."
        classifier_meta = "openai_json" if "OpenAI" in classifier.__class__.__name__ else "typesafe_jev"
        default_metadata = {
            "classifier": classifier_meta,
            "retrieval_strategy": "self_corrective",
        }
        if metadata:
            default_metadata.update(metadata)
        self.info = WorkflowInfo(
            id=id,
            name=name,
            description=desc,
            is_conversational=False,
            supports_streaming=True,
            step_labels=step_labels or dict(DEFAULT_STEP_LABELS),
            generation_nodes=generation_nodes or set(DEFAULT_GENERATION_NODES),
            metadata=default_metadata,
        )
        self._lock = threading.RLock()
        self._compiled_graph = None

    def get_compiled_graph(self) -> Any:
        with self._lock:
            if self._compiled_graph is None:
                self._compiled_graph = create_agentic_rag_graph(
                    classifier=self.classifier,
                    retriever=self.retriever,
                    kb_tool=self.kb_tool,
                    web_tool=self.web_tool,
                )
            return self._compiled_graph

    def to_initial_state(self, request: ChatRequest, trace_id: str | None = None) -> dict[str, Any]:
        return {
            "question": request.question,
            "current_query": "",
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "retry_count": 0,
            "trace": [],
            "citations": [],
            "trace_id": trace_id or request.trace_id,
            "retrieved_contexts": [],
            "attachments": request.attachments or [],
        }

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        from app.api.schemas import ChatResponse
        resolved_tid = trace_id or final_state.get("trace_id")
        return ChatResponse(
            workflow=self.info.id,
            answer=final_state.get("answer") or "",
            source_used=final_state.get("source_used") or "direct",
            trace=final_state.get("trace") or [],
            citations=final_state.get("citations") or [],
            rewritten_query=final_state.get("current_query") or final_state.get("question"),
            trace_id=resolved_tid,
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used") or "direct",
            retrieved_contexts=final_state.get("retrieved_contexts") or [],
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        err_msg = (
            "I encountered an unexpected issue while retrieving or generating information. "
            "Please try again or contact IT support if the problem persists."
        )
        existing_trace = current_state.get("trace", []) if isinstance(current_state.get("trace"), list) else []
        return {
            "question": current_state.get("question", ""),
            "current_query": current_state.get("current_query", ""),
            "kb_docs": current_state.get("kb_docs", []),
            "web_results": current_state.get("web_results", ""),
            "kb_grade": current_state.get("kb_grade", ""),
            "web_grade": current_state.get("web_grade", ""),
            "answer": err_msg,
            "source_used": "error_fallback",
            "retry_count": current_state.get("retry_count", 0),
            "trace": existing_trace + [f"Workflow error fallback: {str(exc)}"],
            "citations": current_state.get("citations", []),
            "retrieved_contexts": current_state.get("retrieved_contexts", []),
        }


__all__ = [
    "DEFAULT_STEP_LABELS",
    "DEFAULT_GENERATION_NODES",
    "SelfCorrectiveRagWorkflow",
]
