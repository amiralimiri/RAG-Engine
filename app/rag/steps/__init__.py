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
from app.rag.steps.defaults import DEFAULT_STEP_CLASSES, register_default_steps
from app.rag.steps.generation import PromptGenerationStep, QueryReformulationStep, format_chat_history
from app.rag.steps.retrieval import ToolRetrievalStep

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
