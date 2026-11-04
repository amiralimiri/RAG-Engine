# Retrospective: ADR-0021 Extract Execution Context Binder Seam

**Milestone:** ADR-0021 (Tickets 01–04)  
**Date:** 2026-11-04  
**Scope:** `app/rag/workflows/binder.py`, `app/rag/workflows/base.py`, `app/rag/workflows/runner.py`, `app/rag/workflows/__init__.py`, `tests/rag/test_execution_context_binder.py`, `tests/rag/test_workflow_runner.py`, `tests/rag/test_workflow_runner_memory.py`, `tests/test_jev_telemetry.py`, `tests/test_llm_telemetry.py`.  
**Test Baseline:** 869 passed tests across the entire repository (0 failures).

---

## 1. What Went Well

### 1.1. Deep Module Extraction & Runner Streamlining
- **Decoupled Pre-Execution Orchestration**: Extracted ~220 lines of procedural parameter resolution, memory hydration, and telemetry setup from `WorkflowRunner` into a cohesive, deep module `ExecutionContextBinder` (`app/rag/workflows/binder.py`).
- **Eliminated Untyped Positional Coupling**: Replaced the legacy untyped 5-tuple return value (`tuple[WorkflowDefinition, str | None, Any, dict[str, Any], dict[str, Any]]`) with an immutable, strongly-typed domain model `BoundExecutionContext` (`frozen=True, extra="forbid"`). Callers across `execute()`, `execute_with_context()`, `aexecute()`, and `stream()` now unpack properties with complete type safety.
- **Convenient LangGraph Integration**: Provided `bound.as_runnable_config()`, returning a defensive copy of `config` with pre-merged callbacks, metadata, and tags ready for direct ingestion by LangGraph `invoke()` and `astream_events()`.

### 1.2. Symmetric Memory Seam Separation
- **Read-Path Hydration Encapsulated**: In ADR-0020, write-path turn persistence was cleanly delegated to `MemoryPersistenceListener`, but read-path turn retrieval remained tangled inside `WorkflowRunner`. ADR-0021 successfully migrated all read-path conversation memory hydration into `ExecutionContextBinder`, completely freeing `WorkflowRunner` from direct interaction with `ConversationMemoryAdapter`.
- **Native Async Event-Loop Hydration**: In `ExecutionContextBinder.abind()`, conversational turns and messages are awaited directly on the active event loop via `memory.aget_messages()` / `memory.aget_turns()`, eliminating unnecessary threadpool hops (`run_async_safely`) during asynchronous streaming and execution.

### 1.3. High-Leverage Testability
- **Isolated Unit Testing Without LangGraph**: 3-tier parameter precedence resolution (Explicit overrides > Registered profile > Workflow baseline), multi-dimensional telemetry tag composition (`workflow:`, `env:`, `variant:`, `model:`, `prompt:`, `turn_count:`), and rewind turn slicing (`rewind_turn_index=k`) can now be tested in fast, isolated unit tests against `ExecutionContextBinder` in milliseconds without spinning up compiled LangGraph runtimes or mocking execution graphs.
- **Backwards Compatibility Preserved**: Seamless constructor auto-wiring in `WorkflowRunner` and read-only property delegations (`runner.binder`, `runner.memory`, `runner.resolve_workflow`) ensured zero disruption to existing external callers and test suites.

---

## 2. Friction Points & Root Causes

### 2.1. Residual Private Forwarding Shims Violating Zero-Shim Architecture
- **Symptom:** During initial ticket implementation, `WorkflowRunner._to_workflow_input` was retained as a private pass-through delegating to `self._binder._to_workflow_input` to avoid touching legacy assertions in `test_workflow_runner.py`.
- **Root Cause:** A tendency to preserve private methods as convenience shims for older tests rather than updating test callers to target the newly established authoritative seam.
- **Resolution:** Caught during the comprehensive code review. Removed `WorkflowRunner._to_workflow_input` and updated test cases to exercise `runner.binder._to_workflow_input` directly, maintaining strict adherence to `docs/agents/codebase-unification.md` §1.1.

### 2.2. Omission of Compiled Telemetry Tags in RunnableConfig
- **Symptom:** While `_compile_telemetry` compiled and deduplicated telemetry tags for Langfuse callback handlers, `run_config["tags"]` inside `BoundExecutionContext.config` only retained user-supplied tags.
- **Root Cause:** Incomplete spec compliance during initial implementation where tags were passed to callback factories but omitted from the runnable config dictionary.
- **Resolution:** Updated `_build_bound_context` in `ExecutionContextBinder` to store compiled multi-dimensional tags in `run_config["tags"]` (merging order-preserving with any user-supplied tags) satisfying ADR-0021 requirements, and aligned legacy telemetry test assertions.

### 2.3. Private Seam Piercing in Test Doubles
- **Symptom:** In `tests/rag/test_workflow_runner_memory.py`, tests directly invoked `await runner.binder._ahydrate_messages(req)`.
- **Root Cause:** Writing test assertions against private internal helper functions rather than the public domain contract.
- **Resolution:** Replaced all internal calls with public contract execution `await runner.binder.abind(req)` and asserted against the resulting `bound.hydrated_messages`.

---

## 3. Process & Environment Action Items

| Action Item | Area | Description | Implementation Strategy |
| :--- | :--- | :--- | :--- |
| **1. Enforce Pure Zero-Shim Extraction** | Architecture | When extracting deep modules, do not retain private pass-through shims (`_helper`) on orchestrators simply to satisfy tests. Update test callers to the authoritative seam. | Enforced in `docs/agents/codebase-unification.md` § 1.1. |
| **2. Pre-Populate RunnableConfig Tags Invariant** | Architecture | Ensure `BoundExecutionContext.config["tags"]` always contains canonical multi-dimensional tags (`workflow:`, `env:`, `variant:`, `model:`, `prompt:`, `turn_count:`) merged with user-supplied tags so LangGraph nodes and downstream hooks receive uniform telemetry. | Documented in `docs/agents/workflow-execution.md` § 10. |
| **3. Test Public Domain Seams, Never Internal Hydration Helpers** | Testing | Test pre-execution conversational memory hydration exclusively through `binder.bind()` or `await binder.abind()` and assert on `bound.hydrated_messages`. Never call `_ahydrate_messages` directly. | Enforced in `docs/agents/workflow-execution.md` § 10. |
| **4. Symmetric Memory Lifecycles** | Architecture | Maintain strict architectural separation between pre-execution read-path memory hydration (`ExecutionContextBinder`) and post-execution write-path turn persistence (`MemoryPersistenceListener`). | Codified in `docs/agents/workflow-execution.md` § 10. |
