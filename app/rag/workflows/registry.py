"""Central thread-safe WorkflowRegistry managing workflow lifecycles and compilation."""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import sys
import threading
from pathlib import Path
from typing import Any, TypeVar

import structlog

from app.rag.workflows.base import ExecutionProfile, PluggableWorkflow, WorkflowDefinition, WorkflowInfo

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class WorkflowRegistry:
    """Thread-safe registry managing workflow instances, metadata, and compilation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._profiles: dict[str, dict[str, ExecutionProfile]] = {}

    def register(self, workflow: WorkflowDefinition, *, override: bool = False) -> None:
        """Register a workflow instance. Raises ValueError on duplicate IDs or validation failure."""
        if not hasattr(workflow, "info") or workflow.info is None:
            raise ValueError("Workflow must have an 'info' attribute with a non-empty 'id'.")

        info = workflow.info
        if not isinstance(info, WorkflowInfo):
            raise ValueError(
                f"Workflow 'info' attribute must be an instance of WorkflowInfo, got {type(info).__name__}."
            )

        if not info.id or not str(info.id).strip():
            raise ValueError("Workflow must have an 'info' attribute with a non-empty 'id'.")

        if info.supports_streaming and (not info.generation_nodes or len(info.generation_nodes) == 0):
            raise ValueError(
                f"Workflow '{info.id}' declares supports_streaming=True but has empty generation_nodes. "
                f"Workflows supporting streaming must declare at least one generation node in generation_nodes."
            )

        wf_id = info.id
        with self._lock:
            if wf_id in self._workflows and not override:
                raise ValueError(
                    f"Workflow with id '{wf_id}' is already registered. "
                    f"Use override=True to replace it."
                )
            self._workflows[wf_id] = workflow
            logger.info("workflow_registered", workflow_id=wf_id, name=info.name)

    def load_from_directory(self, path: Path | str, *, override: bool = False) -> int:
        """Scan subdirectories for manifest.json, dynamically import entrypoints, and register workflows.

        Parameters
        ----------
        path : Path | str
            Directory containing workflow bundle subdirectories (or a bundle directory itself).
        override : bool, optional
            Whether to overwrite already registered workflows with matching IDs. Default False.

        Returns
        -------
        int
            Number of workflows successfully loaded and registered.
        """
        dir_path = Path(path).resolve()
        if not dir_path.exists():
            logger.warning("workflow_directory_not_found", path=str(dir_path))
            return 0
        if not dir_path.is_dir():
            logger.warning("workflow_directory_not_a_directory", path=str(dir_path))
            return 0

        candidate_dirs: list[Path] = []
        if (dir_path / "manifest.json").is_file():
            candidate_dirs.append(dir_path)
        else:
            try:
                entries = sorted(dir_path.iterdir())
            except OSError as err:
                logger.error("workflow_directory_scan_failed", path=str(dir_path), error=str(err))
                return 0

            for entry in entries:
                if entry.is_dir() and not entry.name.startswith((".", "__")):
                    if (entry / "manifest.json").is_file():
                        candidate_dirs.append(entry)
                    else:
                        logger.warning(
                            "workflow_manifest_not_found",
                            bundle=str(entry),
                            message=f"Directory '{entry.name}' does not contain manifest.json, skipping.",
                        )

        loaded_count = 0
        for bundle_dir in candidate_dirs:
            try:
                wf = self._load_bundle(bundle_dir)
                self.register(wf, override=override)
                loaded_count += 1
                logger.info(
                    "workflow_bundle_loaded",
                    workflow_id=wf.info.id,
                    bundle=str(bundle_dir),
                )
            except Exception as exc:
                logger.error(
                    "workflow_bundle_load_failed",
                    bundle=str(bundle_dir),
                    error=str(exc),
                    exc_info=True,
                )

        return loaded_count

    def _load_bundle(self, bundle_dir: Path) -> WorkflowDefinition:
        """Parse bundle manifest and resolve entrypoint callable into a PluggableWorkflow."""
        manifest_file = bundle_dir / "manifest.json"
        if not manifest_file.is_file():
            raise FileNotFoundError(f"Missing manifest.json in '{bundle_dir}'.")

        try:
            content = manifest_file.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as exc:
            raise ValueError(f"Failed to parse manifest.json in '{bundle_dir}': {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"manifest.json in '{bundle_dir}' must contain a JSON object, got {type(data).__name__}."
            )

        entrypoint = data.get("entrypoint")
        if not entrypoint or not isinstance(entrypoint, str) or not entrypoint.strip():
            raise ValueError(f"manifest.json in '{bundle_dir}' is missing non-empty 'entrypoint'.")
        entrypoint = entrypoint.strip()

        if ":" not in entrypoint:
            raise ValueError(
                f"Invalid entrypoint '{entrypoint}' in '{bundle_dir}'. Expected format 'module_name:callable_name'."
            )

        module_spec, callable_name = entrypoint.split(":", 1)
        module_spec = module_spec.strip()
        callable_name = callable_name.strip()
        if not module_spec or not callable_name:
            raise ValueError(
                f"Invalid entrypoint '{entrypoint}' in '{bundle_dir}'. Both module and callable must be non-empty."
            )

        # Parse into WorkflowInfo
        info = WorkflowInfo.model_validate(data)

        # Ensure bundle_dir is in sys.path so sibling imports work
        bundle_dir_str = str(bundle_dir.resolve())
        if bundle_dir_str not in sys.path:
            sys.path.insert(0, bundle_dir_str)

        target_file: Path | None = None
        if module_spec.endswith(".py"):
            cand = bundle_dir / module_spec
            if cand.is_file():
                target_file = cand
        else:
            rel = module_spec.replace(".", "/")
            cand_py = bundle_dir / f"{rel}.py"
            cand_init = bundle_dir / rel / "__init__.py"
            cand_direct = bundle_dir / module_spec
            if cand_py.is_file():
                target_file = cand_py
            elif cand_init.is_file():
                target_file = cand_init
            elif cand_direct.is_file():
                target_file = cand_direct

        if target_file is not None and target_file.is_file():
            clean_bundle = re.sub(r"[^a-zA-Z0-9_]", "_", bundle_dir.name)
            clean_mod = re.sub(r"[^a-zA-Z0-9_]", "_", module_spec)
            unique_mod_name = f"_dynamic_workflow_{clean_bundle}_{clean_mod}"

            spec = importlib.util.spec_from_file_location(
                unique_mod_name,
                str(target_file),
                submodule_search_locations=[str(bundle_dir)] if target_file.name == "__init__.py" else None,
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot initialize module spec from '{target_file}'.")

            mod = importlib.util.module_from_spec(spec)
            sys.modules[unique_mod_name] = mod
            spec.loader.exec_module(mod)
        else:
            mod = importlib.import_module(module_spec)

        if not hasattr(mod, callable_name):
            raise AttributeError(
                f"Module '{module_spec}' in '{bundle_dir}' has no callable attribute '{callable_name}'."
            )

        callable_obj = getattr(mod, callable_name)
        if not callable(callable_obj) and not hasattr(callable_obj, "invoke") and not hasattr(callable_obj, "ainvoke"):
            raise TypeError(
                f"Entrypoint '{entrypoint}' in '{bundle_dir}' resolved to non-callable, non-runnable object of type {type(callable_obj).__name__}."
            )

        wf = PluggableWorkflow(info=info, graph=callable_obj)

        profiles_data = data.get("profiles")
        if isinstance(profiles_data, list):
            for p in profiles_data:
                try:
                    profile_obj = ExecutionProfile.model_validate(p)
                    self.register_profile(info.id, profile_obj)
                except Exception as exc:
                    logger.warning("failed_to_load_profile_from_manifest", workflow_id=info.id, error=str(exc))
        elif isinstance(profiles_data, dict):
            for variant_id, p in profiles_data.items():
                try:
                    if isinstance(p, dict):
                        p_dict = dict(p)
                        p_dict.setdefault("variant_id", variant_id)
                        profile_obj = ExecutionProfile.model_validate(p_dict)
                    elif isinstance(p, ExecutionProfile):
                        profile_obj = p
                    else:
                        continue
                    self.register_profile(info.id, profile_obj)
                except Exception as exc:
                    logger.warning("failed_to_load_profile_from_manifest", workflow_id=info.id, error=str(exc))

        return wf

    def register_profile(self, workflow_id: str, profile: ExecutionProfile) -> None:
        """Register an execution profile for a workflow variant."""
        if not workflow_id or not str(workflow_id).strip():
            raise ValueError("workflow_id must be non-empty.")
        if not isinstance(profile, ExecutionProfile):
            raise ValueError(f"profile must be an ExecutionProfile, got {type(profile).__name__}.")
        if not profile.variant_id or not str(profile.variant_id).strip():
            raise ValueError("Profile variant_id must be non-empty.")

        with self._lock:
            if workflow_id not in self._profiles:
                self._profiles[workflow_id] = {}
            self._profiles[workflow_id][profile.variant_id] = profile
            logger.info(
                "execution_profile_registered",
                workflow_id=workflow_id,
                variant_id=profile.variant_id,
            )

    def get_profile(self, workflow_id: str, variant_id: str) -> ExecutionProfile | None:
        """Retrieve a registered execution profile by workflow ID and variant ID."""
        with self._lock:
            return self._profiles.get(workflow_id, {}).get(variant_id)

    def list_profiles(self, workflow_id: str) -> list[ExecutionProfile]:
        """List all execution profiles registered for a workflow."""
        with self._lock:
            profiles_map = self._profiles.get(workflow_id, {})
            return [profiles_map[k] for k in sorted(profiles_map.keys())]

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
        """Clear all registered workflows and execution profiles (primarily for testing)."""
        with self._lock:
            self._workflows.clear()
            self._profiles.clear()

    def unregister(self, workflow_id: str) -> bool:
        """Remove a workflow by ID from the registry. Returns True if removed, False otherwise."""
        with self._lock:
            self._profiles.pop(workflow_id, None)
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


def load_workflows_from_directory(
    path: Path | str,
    *,
    registry: WorkflowRegistry | None = None,
    override: bool = False,
) -> int:
    """Helper function to load dynamic workflows from directory into a target or default registry."""
    target_registry = registry if registry is not None else _default_registry
    return target_registry.load_from_directory(path, override=override)


__all__ = [
    "WorkflowRegistry",
    "get_workflow_registry",
    "register_workflow",
    "load_workflows_from_directory",
]
