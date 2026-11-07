# 02: Core Step Seam & Catalog Infrastructure

**What to build:**
The core step abstraction layer and centralized registry in `app/rag/steps/base.py`.
Defines the `WorkflowStep` runtime-checkable protocol with `execute` and `__call__` entrypoints, allowing instances to be plugged directly into LangGraph's `graph.add_node(name, step)`.
Implements an abstract `BaseStep` providing dot-notated input and output extraction helpers (`resolve_input`, `apply_outputs`) supporting blackboard `context.*` mapping as well as top-level state keys with sensible zero-config defaults.
Implements the thread-safe `StepCatalog`, the `@register_step(alias)` decorator, and dynamic dotted-path Python import resolution (`module.path:ClassName`) for custom step escape hatches.

**Blocked by:**
01: State & Multimodal Foundations

**Status:**
ready-for-agent

- [x] Create `app/rag/steps/__init__.py` exporting core abstractions.
- [x] Create `app/rag/steps/base.py` declaring `WorkflowStep(Protocol)` with `async def execute(self, state, config) -> dict[str, Any]` and `async def __call__(self, state, config) -> dict[str, Any]`.
- [x] Implement `BaseStep` abstract class with default `__call__` delegating to `execute`, and input/output mapping utilities (`resolve_input`, `apply_outputs`).
- [x] Implement thread-safe `StepCatalog` with registration methods, `get_step_class`, and `resolve_step_type` supporting dotted-path imports.
- [x] Implement `@register_step(alias: str)` decorator.
- [x] Add unit test suite in `tests/rag/steps/test_step_base_and_catalog.py` verifying protocol conformance, dot-path context resolution, registry lookups, and dynamic imports.
- [x] Pass `ruff check` and `pyright` on modified modules.
