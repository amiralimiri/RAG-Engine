"""Backward-compatibility shim for fix_single_llm workflow."""
from typing import Any

from app.rag.classifiers import OpenAIClassifierAdapter
from app.rag.engine import _get_workflow_graph, ask as engine_ask

classifier = OpenAIClassifierAdapter()
agent_graph = _get_workflow_graph("fix_single_llm")


def ask(
    question: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    callback: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute the fix_single_llm agentic RAG workflow."""
    return engine_ask(
        question,
        workflow="fix_single_llm",
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
        callback=callback,
        **kwargs,
    )
