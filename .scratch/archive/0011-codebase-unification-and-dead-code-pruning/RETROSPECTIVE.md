# Retrospective: Milestone 0011 (Comprehensive Codebase Unification, Dead-Code Pruning, and Test Suite Modernization)

**Date:** October 30, 2026  
**Status:** Completed, Reviewed, Remediated, & Archived  
**ADR Reference:** `docs/adr/0011-codebase-unification-and-dead-code-pruning.md`  
**Specification:** `docs/specs/0011-codebase-unification-and-dead-code-pruning-spec.md`  
**Work Packages / Tickets:** `docs/specs/0011-codebase-unification-and-dead-code-pruning-tickets.md`  
**Test Suite:** 457 passed, 0 failed, 100% offline-capable, zero inter-test state leaks  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0011 executed a comprehensive structural purification of the codebase following the domain deepening milestones (0008 BaseTool, 0009 VectorStoreAdapter, 0010 WorkflowRunner). It completely dismantled obsolete backward-compatibility shims, pruned 424 lines of legacy dictionaries, eliminated mutable module-level globals, decoupled tool instantiations from workflow factories, and modernized the entire test suite using isolated Pytest fixtures.

### Key Deliverables:
- **Zero-Shim Domain Architecture**:
  - Permanently removed `app/rag/workflows/legacy.py` (424 lines), `app/rag/fix_single_llm.py`, `app/rag/fix_single_jev.py`, `app/rag/vectorstore.py`, and `app/services/ingestion.py`.
  - Deleted legacy compatibility tests (`tests/rag/test_legacy_compat.py` and `tests/test_indexing_regression.py`).
  - Purged dictionary shims (`WORKFLOW_ASKS`, `WORKFLOW_GRAPHS`, `_LegacyUnifiedWorkflowAdapter`) and residual function facades (`llm()`).
- **Pure Engine Factory (`create_agentic_rag_graph`)**:
  - `app/rag/engine.py` was refactored from 432 lines down to 302 lines.
  - Stripped mutable module globals (`_llm`, `web_search_tool`, `retrieve_kb`, `search_web`, `retriever`, `engine.ask()`).
  - Implemented pure dependency injection: `model`, `kb_tool`, and `web_tool` are injected via factory arguments and bound directly into LangGraph node closures.
- **Dynamic Reflection Elimination**:
  - Stripped `sys.modules.get("app.rag.engine")` runtime inspection from `app/rag/tools/search.py`.
  - Consolidated canonical file parsing formats under `SUPPORTED_EXTENSIONS` in `app/rag/knowledge/`.
- **Modern Test Architecture (`tests/conftest.py`)**:
  - Established a centralized, autouse `clean_registry` fixture that resets `WorkflowRegistry` before and after every test, preventing cross-test pollution without ad-hoc teardowns.
  - Implemented `MockWorkflowDefinition` test double and `register_mock_workflow` fixture, completely replacing fragile `unittest.mock.patch.dict` module monkeypatching.
  - Modernized 14 test suites across route testing, observability, streaming, token cost accounting, and telemetry spans.

---

## 2. What Went Well

1. **Substantive Test Modernization Over Surface Mocking**:
   - Instead of keeping tests coupled to implementation details or deleted legacy module globals, test suites were elevated to test first-class seams (`WorkflowRunner`, `WorkflowRegistry`, `BaseTool`).
2. **Deterministic Test Isolation**:
   - The autouse `clean_registry` fixture immediately resolved order-dependent test failures and eliminated the need for individual tests to clean up registered workflow definitions.
3. **Rigorous Two-Axis Review & Fast Remediation**:
   - The `/code-review` process effectively caught a lingering `llm()` backward-compatibility facade in `app/rag/engine.py` and dead code in `evaluation/run_experiment.py`, both of which were cleanly pruned in the remediation pass.
4. **Complete Invariance of Domain Behavior**:
   - All 457 tests across synchronous chat, SSE token streaming, Langfuse telemetry spans, and token cost accounting pass green with zero breaking changes to API schemas.

---

## 3. Friction Points & Challenges Encountered

1. **Lingering Compatibility Instincts (`llm()`)**:
   - *Issue*: During the initial pruning of `engine.py`, a helper `def llm() -> BaseChatModel: return get_chat_model()` was added "just in case" callers needed it, directly violating the Zero-Shim standard.
   - *Resolution*: Flagged in `/code-review` and permanently removed; callers use `get_chat_model()` or pass injected model instances directly.
2. **Hidden Test Dependencies on Module Dictionaries**:
   - *Issue*: Several older tests patched `WORKFLOWS` or `app.rag.engine.llm` via monkeypatching. When those symbols were deleted, tests failed due to attribute errors.
   - *Resolution*: Replaced monkeypatching with direct constructor injection (`SelfCorrectiveRagWorkflow(model=fake_llm)`) or registry overrides via `register_mock_workflow()`.
3. **Typing Drift (`Optional[T]` vs `T | None`)**:
   - *Issue*: `evaluation/run_experiment.py` slipped in `Optional` hints from `typing` rather than standard PEP 604 union syntax (`| None`).
   - *Resolution*: Modernized all type signatures to PEP 604 unions across the codebase.

---

## 4. Codified Agent Guardrails & Updated Rules

To prevent regression in future milestones, the following rules are established in `docs/agents/codebase-unification.md` and `AGENTS.md`:

1. **Zero-Shim Policy**:
   - Never create or retain backward-compatibility wrappers, pass-through facades, or legacy dictionary adapters. If a symbol is deprecated, update all callers to use the canonical seam and delete the deprecated symbol.
2. **Strict Constructor & Parameter DI**:
   - Never introduce module-level mutable globals (`_llm = None`, `_tool = None`) to share state or dependencies. All workflow factories must accept dependencies as explicit parameters and capture them in node closures.
3. **Zero `sys.modules` Reflection**:
   - Production code must never inspect `sys.modules` to detect test doubles or module-level mocks. Dependencies must be passed explicitly.
4. **Fixture-Based Test Doubles**:
   - Never monkeypatch module dictionaries (`patch.dict(...)`). Always register test doubles into `WorkflowRegistry` using `register_mock_workflow()` or inject test doubles into `WorkflowRunner` / workflow constructors.
5. **PEP 604 Type Unions**:
   - Always use `T | None` instead of `Optional[T]`.

---

## 5. Artifact & Ticket Archive Status

All 7 implementation work packages from `.scratch/0011-codebase-unification-and-dead-code-pruning/` have been moved to `.scratch/archive/0011-codebase-unification-and-dead-code-pruning/`:

| Ticket | Scope | Status |
|---|---|---|
| **Ticket 01** | Test Isolation Fixture Foundation (`tests/conftest.py`) | Completed & Archived |
| **Ticket 02** | Canonical Ingestion & Storage Facade Pruning | Completed & Archived |
| **Ticket 03** | Pure Engine Factory & Tool Decoupling | Completed & Archived |
| **Ticket 04** | Legacy Workflow Shims & Dictionary Deletion | Completed & Archived |
| **Ticket 05** | Route & Observability Test Suite Modernization | Completed & Archived |
| **Ticket 06** | Telemetry, Contexts & Spans Test Suite Consolidation | Completed & Archived |
| **Ticket 07** | Full Verification & E2E Regression Gate | Completed & Archived |

---

## 6. Future Architectural Opportunities

1. **Workflow Composition DSL & Config-Driven Topologies**:
   - With `WorkflowDefinition` and `WorkflowRegistry` fully isolated, investigate declarative workflow configurations (e.g. YAML/JSON or Pydantic graphs) for dynamically defining routing, grading nodes, and model temperatures without custom Python graph code.
2. **LangGraph v1 -> v2 Migration**:
   - Address remaining deprecation warnings around `create_react_agent` (which is moving to `langchain.agents.create_agent` in LangGraph 2.0).
3. **Async Evaluation Pipeline Optimization**:
   - Further optimize the asynchronous background evaluation tasks spawned in `WorkflowRunner` using a dedicated worker pool or Redis/Celery queue for higher-throughput production deployments.
