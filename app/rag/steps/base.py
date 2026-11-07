"""Core step abstractions and catalog infrastructure for declarative workflows.

Defines the WorkflowStep protocol seam, BaseStep abstract base class with dot-notated
input/output mapping, and the thread-safe StepCatalog for step discovery and dynamic imports.
"""

import abc
import copy
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
        outputs: dict[str, Any],
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Map step outputs into a pure delta update dictionary.

        Supports writing to both top-level state fields and nested paths (like 'context.evidence')
        without mutating the caller's ``state``. When ``state`` is provided, existing data at the
        target root key is deep-copied into the delta so LangGraph last-value channels preserve
        unrelated sibling keys.
        """
        delta: dict[str, Any] = {}

        for key, val in outputs.items():
            target = self.outputs.get(key, key)
            if not target:
                continue

            root_key, separator, nested_path = target.partition(".")
            if not separator:
                delta[root_key] = val
                continue

            if root_key not in delta or not isinstance(delta[root_key], dict):
                existing = state.get(root_key) if isinstance(state, dict) else None
                delta[root_key] = copy.deepcopy(existing) if isinstance(existing, dict) else {}

            _write_dot_path(delta[root_key], nested_path, val)

        return delta


def _write_dot_path(root: dict[str, Any], path: str, value: Any) -> None:
    """Write ``value`` into ``root`` at a dot-notated path, creating intermediate dicts."""
    parts = path.split(".")
    current = root

    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested

    current[parts[-1]] = value


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
_default_step_catalog = StepCatalog()


def get_step_catalog() -> StepCatalog:
    """Return the global default StepCatalog singleton."""
    return _default_step_catalog


def reset_step_catalog() -> None:
    """Reset the global default StepCatalog singleton for test isolation."""
    _default_step_catalog.clear()


StepClassT = TypeVar("StepClassT", bound=type[WorkflowStep])


def register_step(alias: str, *, catalog: StepCatalog | None = None) -> Callable[[StepClassT], StepClassT]:
    """Decorator to register a WorkflowStep class in the StepCatalog."""

    def decorator(cls: StepClassT) -> StepClassT:
        target_catalog = catalog if catalog is not None else get_step_catalog()
        target_catalog.register(alias, cls)
        return cls

    return decorator


def get_step_class(alias: str, *, catalog: StepCatalog | None = None) -> type[WorkflowStep] | None:
    """Convenience helper to retrieve a step class from the default or specified catalog."""
    target_catalog = catalog if catalog is not None else get_step_catalog()
    return target_catalog.get_step_class(alias)


def resolve_step_type(identifier: str, *, catalog: StepCatalog | None = None) -> type[WorkflowStep]:
    """Convenience helper to resolve a step class from the default or specified catalog."""
    target_catalog = catalog if catalog is not None else get_step_catalog()
    return target_catalog.resolve_step_type(identifier)
