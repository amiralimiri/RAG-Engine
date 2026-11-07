"""Core step abstractions and catalog infrastructure for declarative workflows.

Defines the WorkflowStep protocol seam, BaseStep abstract base class with dot-notated
input/output mapping, and the thread-safe StepCatalog for step discovery and dynamic imports.
"""

from __future__ import annotations

import abc
import importlib
import threading
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

import structlog
from langchain_core.runnables import RunnableConfig

logger = structlog.get_logger(__name__)


@runtime_checkable
class WorkflowStep(Protocol):
    """Protocol seam defining execution contracts for discrete workflow nodes."""

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Execute step logic against state and configuration, returning delta state updates."""
        ...

    async def __call__(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """LangGraph node compatibility entrypoint, delegating directly to execute."""
        ...


class BaseStep(abc.ABC):
    """Abstract base class for workflow steps with dot-notated mapping and LangGraph callable support."""

    def __init__(
        self,
        *,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        self.name = name or self.__class__.__name__
        self.inputs = dict(inputs) if inputs else {}
        self.outputs = dict(outputs) if outputs else {}

    @abc.abstractmethod
    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """Execute step logic against state and configuration, returning delta updates."""
        raise NotImplementedError

    async def __call__(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        """LangGraph node compatibility entrypoint, delegating directly to execute."""
        return await self.execute(state, config)

    def resolve_input(
        self,
        state: dict[str, Any],
        key: str,
        default: Any = None,
    ) -> Any:
        """Resolve an input value from state using configured mapping or key fallback.

        Supports dot-notated paths (e.g. 'context.agent.query') safely traversing nested
        dictionaries without raising KeyError when intermediate or leaf keys are missing,
        returning the default value instead.
        """
        if not isinstance(state, dict):
            return default

        path = self.inputs.get(key, key)
        if not path:
            return default

        parts = path.split(".")
        curr: Any = state

        for part in parts:
            if not isinstance(curr, dict) or part not in curr:
                return default
            curr = curr[part]

        return curr

    def apply_outputs(
        self,
        state_or_outputs: dict[str, Any],
        outputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply step outputs into state, returning delta updates.

        Supports writing to both top-level state fields and nested paths (like 'context.evidence').
        Preserves existing data at intermediate levels.
        """
        if outputs is None:
            state: dict[str, Any] = {}
            step_outputs = state_or_outputs
        else:
            state = state_or_outputs
            step_outputs = outputs

        delta: dict[str, Any] = {}

        for key, val in step_outputs.items():
            target = self.outputs.get(key, key)
            if not target:
                continue

            parts = target.split(".")
            if len(parts) == 1:
                field = parts[0]
                delta[field] = val
                if isinstance(state, dict):
                    state[field] = val
            else:
                root_key = parts[0]
                if root_key not in delta:
                    existing = state.get(root_key) if isinstance(state, dict) else None
                    root_dict = dict(existing) if isinstance(existing, dict) else {}
                    delta[root_key] = root_dict
                else:
                    root_dict = delta[root_key]
                    if not isinstance(root_dict, dict):
                        root_dict = {}
                        delta[root_key] = root_dict

                curr_delta = root_dict
                for part in parts[1:-1]:
                    if part not in curr_delta or not isinstance(curr_delta[part], dict):
                        curr_delta[part] = {}
                    else:
                        curr_delta[part] = dict(curr_delta[part])
                    curr_delta = curr_delta[part]
                curr_delta[parts[-1]] = val

                if isinstance(state, dict):
                    if root_key not in state or not isinstance(state[root_key], dict):
                        state[root_key] = {}
                    curr_state = state[root_key]
                    for part in parts[1:-1]:
                        if part not in curr_state or not isinstance(curr_state[part], dict):
                            curr_state[part] = {}
                        curr_state = curr_state[part]
                    curr_state[parts[-1]] = val

        return delta


class StepCatalog:
    """Thread-safe catalog managing workflow step registrations and dynamic resolution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._steps: dict[str, type[WorkflowStep]] = {}

    def register(
        self,
        alias: str,
        step_cls: type[WorkflowStep],
        *,
        override: bool = False,
    ) -> None:
        """Register a step class with an alias.

        Raises ValueError on empty alias or duplicate registrations (unless override=True).
        Raises TypeError if step_cls is not a class implementing WorkflowStep.
        """
        if not alias or not isinstance(alias, str) or not alias.strip():
            raise ValueError("Step alias must be a non-empty string.")

        if not isinstance(step_cls, type):
            raise TypeError(f"Registered step must be a class, got instance of {type(step_cls).__name__}.")

        if not issubclass(step_cls, WorkflowStep):
            raise TypeError(f"Class '{step_cls.__name__}' does not implement the WorkflowStep protocol.")

        clean_alias = alias.strip()
        with self._lock:
            if clean_alias in self._steps and not override:
                raise ValueError(
                    f"Step with alias '{clean_alias}' is already registered. Use override=True to replace it."
                )
            self._steps[clean_alias] = step_cls
            logger.debug("step_registered", alias=clean_alias, step_class=step_cls.__name__)

    def get_step_class(self, alias: str) -> type[WorkflowStep] | None:
        """Retrieve a registered step class by alias, or None if not found."""
        clean_alias = alias.strip() if isinstance(alias, str) else ""
        with self._lock:
            return self._steps.get(clean_alias)

    def resolve_step_type(self, identifier: str) -> type[WorkflowStep]:
        """Resolve a step class by alias or dynamic dotted Python path ('module:Class' or 'module.Class').

        Raises KeyError if the alias is not found.
        Raises TypeError if the resolved object is not a class implementing WorkflowStep.
        Raises AttributeError or ImportError if dynamic resolution fails.
        """
        if not identifier or not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("Step identifier must be a non-empty string.")

        clean_id = identifier.strip()

        # 1. Check registered alias under lock
        with self._lock:
            if clean_id in self._steps:
                return self._steps[clean_id]

        # 2. Check for dotted Python path escape hatch
        if ":" in clean_id:
            mod_name, cls_name = clean_id.split(":", 1)
        elif "." in clean_id:
            mod_name, cls_name = clean_id.rsplit(".", 1)
        else:
            raise KeyError(f"Step alias '{clean_id}' is not registered in StepCatalog.")

        mod_name = mod_name.strip()
        cls_name = cls_name.strip()

        if not mod_name or not cls_name:
            raise ValueError(f"Invalid dynamic import identifier format: '{identifier}'.")

        try:
            module = importlib.import_module(mod_name)
        except Exception as err:
            logger.error("step_dynamic_import_failed", module=mod_name, error=str(err))
            raise ImportError(f"Could not import module '{mod_name}' for step '{identifier}': {err}") from err

        if not hasattr(module, cls_name):
            raise AttributeError(f"Module '{mod_name}' has no class or attribute '{cls_name}'.")

        resolved = getattr(module, cls_name)
        if not isinstance(resolved, type) or not issubclass(resolved, WorkflowStep):
            raise TypeError(f"Resolved object '{identifier}' must be a class implementing WorkflowStep protocol.")

        return resolved

    def unregister(self, alias: str) -> bool:
        """Unregister a step class by alias. Returns True if removed, False otherwise."""
        clean_alias = alias.strip() if isinstance(alias, str) else ""
        with self._lock:
            return self._steps.pop(clean_alias, None) is not None

    def list_steps(self) -> list[str]:
        """Return a sorted list of all registered step aliases."""
        with self._lock:
            return sorted(self._steps.keys())

    def clear(self) -> None:
        """Clear all registered steps from the catalog."""
        with self._lock:
            self._steps.clear()


# Default singleton instance for application-wide step registration
default_step_catalog = StepCatalog()

StepClassT = TypeVar("StepClassT", bound=type[WorkflowStep])


def register_step(alias: str, *, catalog: StepCatalog | None = None) -> Callable[[StepClassT], StepClassT]:
    """Decorator to register a WorkflowStep class in the StepCatalog."""

    def decorator(cls: StepClassT) -> StepClassT:
        target_catalog = catalog if catalog is not None else default_step_catalog
        target_catalog.register(alias, cls)
        return cls

    return decorator


def get_step_class(alias: str, *, catalog: StepCatalog | None = None) -> type[WorkflowStep] | None:
    """Convenience helper to retrieve a step class from the default or specified catalog."""
    target_catalog = catalog if catalog is not None else default_step_catalog
    return target_catalog.get_step_class(alias)


def resolve_step_type(identifier: str, *, catalog: StepCatalog | None = None) -> type[WorkflowStep]:
    """Convenience helper to resolve a step class from the default or specified catalog."""
    target_catalog = catalog if catalog is not None else default_step_catalog
    return target_catalog.resolve_step_type(identifier)
