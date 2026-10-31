# 03: Dynamic Manifest + Factory Discovery in `WorkflowRegistry`

**What to build:**
Modernize `WorkflowRegistry` in `app/rag/workflows/registry.py` to support dynamic loading and pre-flight validation:
1. Add pre-flight validation in `WorkflowRegistry.register(workflow)` ensuring valid `WorkflowInfo`, non-empty ID, and matching `supports_streaming` $\to$ non-empty `generation_nodes`.
2. Implement `load_from_directory(path: Path | str) -> int`:
   - Scans subdirectories for `manifest.json`.
   - Parses manifest into `WorkflowInfo` and extracts the entrypoint callable (e.g. `module_name:get_graph`).
   - Dynamically imports the module and instantiates a `PluggableWorkflow`.
   - Registers the loaded workflow into the registry and returns the count of loaded workflows.
   - Gracefully handles and logs errors for corrupted or missing files without crashing the entire registry.
3. Add unit and integration tests in `tests/rag/test_registry_loader.py` using `tmp_path` to verify discovery, loading, and error handling.

**Blocked by:** 02: `StandardWorkflowState` & `PluggableWorkflow` Implementation

**Status:** open

- [ ] Pre-flight validation in `WorkflowRegistry.register()`
- [ ] `WorkflowRegistry.load_from_directory()` implemented with dynamic import resolution
- [ ] Comprehensive unit tests in `tests/rag/test_registry_loader.py` covering valid bundles, invalid manifests, and syntax error modules
- [ ] Verification suite: `uv run pytest tests/rag/test_registry_loader.py`
