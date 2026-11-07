"""Canonical built-in workflow step registry and catalog repopulation seam."""

from app.rag.steps.base import StepCatalog, WorkflowStep, get_step_catalog
from app.rag.steps.classifier import ClassifierGradingStep, ClassifierRouteStep
from app.rag.steps.generation import PromptGenerationStep, QueryReformulationStep
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


__all__ = ["DEFAULT_STEP_CLASSES", "register_default_steps"]
