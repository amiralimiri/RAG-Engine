"""Backward-compatibility shim for fix_single_jev workflow."""
from typing import Any

from app.rag.classifiers import JevClassifierAdapter
from app.rag.engine import ask as engine_ask
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow

classifier = JevClassifierAdapter()

_registry = get_workflow_registry()
if "fix_single_jev" not in _registry:
    _registry.register(
        SelfCorrectiveRagWorkflow(
            id="fix_single_jev",
            name="Self-Corrective RAG (JEV)",
            classifier=classifier,
            description="Canonical 10-node self-corrective RAG workflow using TypeSafe JEV classifier for routing and grading.",
        ),
        override=True,
    )
agent_graph = _registry.get("fix_single_jev").get_compiled_graph()


def ask(
    question: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    callback: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute the fix_single_jev agentic RAG workflow via WorkflowRunner."""
    return engine_ask(
        question,
        workflow="fix_single_jev",
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
        callback=callback,
        **kwargs,
    )
