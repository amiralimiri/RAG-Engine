"""Declarative workflow steps package.

Exports the core step protocol seam, base step implementation, step catalog, and built-in
concrete steps for retrieval, classification, prompt generation, and query reformulation.
"""

from app.rag.steps.base import (
    BaseStep,
    StepCatalog,
    WorkflowStep,
    get_step_catalog,
    get_step_class,
    register_step,
    reset_step_catalog,
    resolve_step_type,
)
from app.rag.steps.classifier import ClassifierGradingStep, ClassifierRouteStep
from app.rag.steps.generation import PromptGenerationStep, QueryReformulationStep, format_chat_history
from app.rag.steps.retrieval import ToolRetrievalStep

DEFAULT_STEP_CLASSES: dict[str, type[WorkflowStep]] = {
    "tool_retrieval": ToolRetrievalStep,
    "classifier_route": ClassifierRouteStep,
    "classifier_grade": ClassifierGradingStep,
    "prompt_generate": PromptGenerationStep,
    "query_reformulate": QueryReformulationStep,
}


def register_default_steps(catalog: StepCatalog | None = None) -> None:
    """Register canonical built-in steps into the specified or global catalog.

    Decorators register the same classes on initial module import; this function is the
    authoritative repopulation seam used by ``reset_step_catalog()`` after catalog clears.
    """
    target_catalog = catalog if catalog is not None else get_step_catalog()
    for alias, step_cls in DEFAULT_STEP_CLASSES.items():
        target_catalog.register(alias, step_cls, override=True)


__all__ = [
    "WorkflowStep",
    "BaseStep",
    "StepCatalog",
    "get_step_catalog",
    "reset_step_catalog",
    "register_step",
    "get_step_class",
    "resolve_step_type",
    "register_default_steps",
    "DEFAULT_STEP_CLASSES",
    "ToolRetrievalStep",
    "ClassifierRouteStep",
    "ClassifierGradingStep",
    "PromptGenerationStep",
    "QueryReformulationStep",
    "format_chat_history",
]
