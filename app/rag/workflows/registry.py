"""Central thread-safe WorkflowRegistry managing workflow lifecycles and compilation."""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar
import structlog

from app.rag.workflows.base import WorkflowDefinition, WorkflowInfo

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class WorkflowRegistry:
    """Thread-safe registry managing workflow instances, metadata, and compilation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workflows: dict[str, WorkflowDefinition] = {}

    def register(self, workflow: WorkflowDefinition, *, override: bool = False) -> None:
        """Register a workflow instance. Raises ValueError on duplicate IDs unless override=True."""
        if not hasattr(workflow, "info") or not getattr(workflow.info, "id", None):
            raise ValueError("Workflow must have an 'info' attribute with a non-empty 'id'.")

        wf_id = workflow.info.id
        with self._lock:
            if wf_id in self._workflows and not override:
                raise ValueError(
                    f"Workflow with id '{wf_id}' is already registered. "
                    f"Use override=True to replace it."
                )
            self._workflows[wf_id] = workflow
            logger.info("workflow_registered", workflow_id=wf_id, name=workflow.info.name)

    def get(self, workflow_id: str) -> WorkflowDefinition:
        """Retrieve a registered workflow by ID. Raises KeyError if not found."""
        with self._lock:
            if workflow_id not in self._workflows:
                available = self.list_ids()
                raise KeyError(
                    f"Workflow '{workflow_id}' not found in registry. "
                    f"Available workflows: {available}"
                )
            return self._workflows[workflow_id]

    def list_ids(self) -> list[str]:
        """Return a sorted list of registered workflow identifiers."""
        with self._lock:
            return sorted(self._workflows.keys())

    def list_info(self) -> list[WorkflowInfo]:
        """Return a list of WorkflowInfo metadata sorted by workflow ID."""
        with self._lock:
            return [self._workflows[wf_id].info for wf_id in self.list_ids()]

    def warmup(self) -> None:
        """Pre-compile all registered workflows to eliminate cold-start latency."""
        with self._lock:
            workflows = list(self._workflows.values())

        for wf in workflows:
            logger.info("workflow_warmup_start", workflow_id=wf.info.id)
            wf.get_compiled_graph()
            logger.info("workflow_warmup_complete", workflow_id=wf.info.id)

    def clear(self) -> None:
        """Clear all registered workflows (primarily for testing)."""
        with self._lock:
            self._workflows.clear()

    def unregister(self, workflow_id: str) -> bool:
        """Remove a workflow by ID from the registry. Returns True if removed, False otherwise."""
        with self._lock:
            if workflow_id in self._workflows:
                del self._workflows[workflow_id]
                logger.info("workflow_unregistered", workflow_id=workflow_id)
                return True
            return False

    def __contains__(self, workflow_id: str) -> bool:
        with self._lock:
            return workflow_id in self._workflows

    def __len__(self) -> int:
        with self._lock:
            return len(self._workflows)


_default_registry = WorkflowRegistry()


def get_workflow_registry() -> WorkflowRegistry:
    """Return the global default WorkflowRegistry singleton."""
    return _default_registry


def register_workflow(
    target: Any = None,
    *,
    registry: WorkflowRegistry | None = None,
    override: bool = False,
) -> Any:
    """Decorator or function to register a workflow or workflow class into a registry.

    Usage as a function:
        register_workflow(my_workflow_instance)

    Usage as a decorator on a class (instantiates with default constructor):
        @register_workflow
        class MyWorkflow: ...

    Usage with arguments:
        @register_workflow(override=True)
        class MyWorkflow: ...
    """
    target_registry = registry if registry is not None else _default_registry

    def _decorator(obj: Any) -> Any:
        if isinstance(obj, type):
            instance = obj()
        else:
            instance = obj
        target_registry.register(instance, override=override)
        return obj

    if target is None:
        return _decorator
    return _decorator(target)


__all__ = [
    "WorkflowRegistry",
    "get_workflow_registry",
    "register_workflow",
]
