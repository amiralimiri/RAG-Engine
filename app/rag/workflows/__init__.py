"""Workflows package for multi-workflow agentic execution."""

from app.rag.classifiers import JevClassifierAdapter, OpenAIClassifierAdapter
from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo
from app.rag.workflows.react_agent import ReActAgentWorkflow
from app.rag.workflows.registry import (
    WorkflowRegistry,
    get_workflow_registry,
    register_workflow,
)
from app.rag.workflows.runner import WorkflowRunner, get_workflow_runner
from app.rag.workflows.self_corrective import SelfCorrectiveRagWorkflow


def register_default_workflows(registry: WorkflowRegistry | None = None) -> None:
    """Register canonical default workflows into the specified or global registry."""
    target_registry = registry if registry is not None else get_workflow_registry()

    workflow_llm = SelfCorrectiveRagWorkflow(
        id="fix_single_llm",
        name="Self-Corrective RAG (OpenAI)",
        classifier=OpenAIClassifierAdapter(),
        description="Canonical 10-node self-corrective RAG workflow using OpenAI structured outputs for routing and grading.",
    )
    workflow_jev = SelfCorrectiveRagWorkflow(
        id="fix_single_jev",
        name="Self-Corrective RAG (JEV)",
        classifier=JevClassifierAdapter(),
        description="Canonical 10-node self-corrective RAG workflow using TypeSafe JEV classifier for routing and grading.",
    )
    workflow_react = ReActAgentWorkflow(
        id="react_agent",
        name="Autonomous ReAct Agent",
        description="Dynamic multi-turn autonomous tool-calling agent with memory, private KB retrieval, and web search.",
    )

    target_registry.register(workflow_llm, override=True)
    target_registry.register(workflow_jev, override=True)
    target_registry.register(workflow_react, override=True)


# Auto-register defaults on package import
register_default_workflows()

__all__ = [
    "WorkflowInfo",
    "EvalContext",
    "WorkflowDefinition",
    "WorkflowRegistry",
    "get_workflow_registry",
    "register_workflow",
    "WorkflowRunner",
    "get_workflow_runner",
    "SelfCorrectiveRagWorkflow",
    "ReActAgentWorkflow",
    "register_default_workflows",
]
