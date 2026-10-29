"""Backward-compatibility shim for fix_single_llm workflow."""
from typing import Any

from app.rag.classifiers import OpenAIClassifierAdapter
from app.rag.engine import ask as engine_ask
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow

classifier = OpenAIClassifierAdapter()

_registry = get_workflow_registry()
if "fix_single_llm" not in _registry:
    _registry.register(
        SelfCorrectiveRagWorkflow(
            id="fix_single_llm",
            name="Self-Corrective RAG (OpenAI)",
            classifier=classifier,
            description="Canonical 10-node self-corrective RAG workflow using OpenAI structured outputs for routing and grading.",
        ),
        override=True,
    )
agent_graph = _registry.get("fix_single_llm").get_compiled_graph()


def ask(
    question: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    callback: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute the fix_single_llm agentic RAG workflow via WorkflowRunner."""
    return engine_ask(
        question,
        workflow="fix_single_llm",
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
        callback=callback,
        **kwargs,
    )
