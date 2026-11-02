# Retrospective: Milestone 0018 (Unified Prompt Store Protocol Seam and Zero-Shim Migration)

**Date:** November 2, 2026  
**Status:** Completed, Reviewed, Formatted, & Archived  
**ADR Reference:** `docs/adr/0018-unified-prompt-store-protocol-seam.md`  
**Specification:** `docs/spec.md` & `docs/specs/0018-unified-prompt-store-protocol-seam-spec.md`  
**Work Packages / Tickets:** Tickets 01–04 (Archived under `.scratch/archive/0018-unified-prompt-store-protocol-seam/issues/`)  
**Domain Guidelines:** `docs/agents/prompt-store.md` | `docs/agents/codebase-unification.md` | `CODING_STANDARDS.md`  
**Test Suite:** 726 passed, 0 failed, 100% offline-capable, clean ruff check & format across workspace  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0018 successfully consolidated prompt template management, dynamic mustache interpolation, Langfuse Prompt CMS integration, and observation metadata configuration behind a unified, strongly-typed `PromptStore` protocol seam in `app/rag/prompts/`.

Prior to ADR-0018, prompt management lived in `app/services/prompts.py` as a legacy procedural module relying on mutable module globals (`LOCAL_PROMPTS`, `_seeded_prompts`), manual fallback type inspections (checking `is_fallback` on heterogeneous return types), repetitive observation metadata construction across engine nodes and classifier adapters, and pervasive test monkeypatching.

With this release:
1. Prompt resolution, template interpolation, and Langfuse observation binding are unified behind `PromptStore.render(name, config=None, **variables)` returning a `RenderedPrompt` value object.
2. `RenderedPrompt` encapsulates prompt text, canonical name, backing CMS prompt client, and pre-merged `RunnableConfig` observation metadata, implementing `__str__` for seamless string coercion.
3. Fast, offline, deterministic unit testing is guaranteed via `LocalPromptStore` and the autouse/injected `fake_prompt_store` Pytest fixture.
4. Production integration with Langfuse Prompt CMS is encapsulated in `LangfusePromptStore` with thread-safe TTL caching, fail-open local fallback, and idempotent startup seeding.
5. All consumers across `app/rag/engine.py`, `app/rag/classifiers.py`, `app/rag/workflows/self_corrective.py`, and `app/main.py` consume `PromptStore` via explicit constructor/factory dependency injection.
6. In strict accordance with ADR-0011 zero-shim architecture, `app/services/prompts.py` has been permanently deleted with zero lingering references or backward-compatibility wrappers.
7. The full workspace test suite expanded from 678 to 726 passing tests with zero regressions.

---

## 2. Key Deliverables Across Tickets

### Ticket 01: Core PromptStore Seam, RenderedPrompt Model & Local Adapter
- Created `app/rag/prompts/base.py` declaring the `@runtime_checkable` `PromptStore` protocol seam (`render`, `get_template`, `seed_prompts`) and all 11 canonical templates in `CANONICAL_PROMPTS`.
- Implemented `RenderedPrompt(BaseModel)` with `__str__` returning rendered text and `_clone_config()` for defensive cloning of nested metadata, tags, and callbacks.
- Built a shared, deterministic mustache interpolation engine resilient to whitespace variations (e.g. `{{  question  }}`), regex backslash escapes, and missing variable preservation.
- Implemented `LocalPromptStore` in `app/rag/prompts/local.py` for offline testing with optional template overrides and dynamic registration.
- Added foundational unit tests in `tests/rag/test_prompt_store.py`.

### Ticket 02: Langfuse CMS Production Adapter, Factory & Test Fixtures
- Implemented `LangfusePromptStore` in `app/rag/prompts/langfuse.py` with in-memory TTL caching, thread synchronization locks, automatic fail-open fallback, and observation metadata linking (`metadata["langfuse_prompt"]`).
- Implemented idempotent prompt seeding via `seed_prompts()` querying Langfuse with `cache_ttl_seconds=0` and creating missing prompts with label `"production"`.
- Provided dependency injection lifecycle helpers (`get_prompt_store`, `reset_prompt_store`) in `app/rag/prompts/__init__.py`.
- Integrated `reset_prompt_store()` into `tests/conftest.py` autouse cleanup and provided the `fake_prompt_store` fixture.
- Added comprehensive unit tests in `tests/rag/test_langfuse_prompt_store.py`.

### Ticket 03: Dependency Injection in Workflow Engine, Classifiers & Main Startup
- Parameterized `create_agentic_rag_graph`, `node_condense_query`, `direct_answer`, and `_prepare_generation_prompt` in `app/rag/engine.py` with injected `prompt_store`.
- Parameterized `OpenAIClassifierAdapter` and `JevClassifierAdapter` in `app/rag/classifiers.py` with injected `prompt_store`.
- Wired prompt store parameter through `SelfCorrectiveRagWorkflow` and exposed `.prompt_store` property.
- Pruned obsolete procedural `_build_prompt_config` helper and `is_fallback` checks from classifiers and engine.
- Updated `app/main.py` lifespan to invoke `get_prompt_store().seed_prompts()`.

### Ticket 04: Zero-Shim Pruning of `app/services/prompts.py` & Test Suite Modernization
- Modernized `tests/test_prompts_cms.py` to test against the unified `PromptStore` seam without monkeypatching module globals.
- Migrated all import sites across tests and application modules to `app.rag.prompts`.
- Permanently deleted `app/services/prompts.py`.
- Verified 726 tests pass with 0 failures across the workspace.

---

## 3. What Went Well

1. **High-Leverage Seam Unification**:
   - Consolidating template retrieval, mustache compilation, fallback selection, and `RunnableConfig` observation metadata construction into `prompt_store.render(...)` radically simplified callers. Nodes and classifiers no longer care whether prompts are local fallbacks or remote CMS objects.

2. **Clean Zero-Shim Pruning**:
   - Removing `app/services/prompts.py` entirely eliminated technical debt and legacy baggage. There are no compatibility aliases, deprecated forwarders, or deprecated module stubs left behind.

3. **Fast Offline Determinism**:
   - `LocalPromptStore` and `fake_prompt_store` enable unit and integration tests to execute instantly without network overhead or Langfuse credentials.

4. **Thread-Safe Production Adapter**:
   - `LangfusePromptStore` incorporates an internal instance lock (`threading.RLock`) to guard TTL cache updates and remote fetches against multithreaded stampedes.

---

## 4. Friction Points & Lessons Learned

1. **Defensive Cloning of Nested Observation Configs**:
   - *Friction*: Initially, `RenderedPrompt.with_text` performed a shallow copy (`dict(self.config)`). When downstream nodes modified `config["metadata"]` or attached callbacks, mutations mutated the original dictionary, risking cross-node data bleeding.
   - *Lesson*: When handling nested configurations (like LangChain's `RunnableConfig` with nested dictionaries and lists), always provide explicit deep-cloning (`_clone_config`) for dictionaries and lists so derived domain models are truly immutable.

2. **Keeping Downstream Tests Green During Refactorings**:
   - *Friction*: When parameterizing `create_agentic_rag_graph` and classifiers, downstream tests in `test_retrieved_contexts.py`, `test_retriever_search_spans.py`, `test_token_cost_accounting.py`, and `test_workflow_engine_memory.py` broke because they relied on monkeypatching `get_workflow_prompt` on deleted modules. Additionally, contract tests asserting exact call arguments on graph creation failed until `prompt_store` was accounted for in workflow wrappers (`SelfCorrectiveRagWorkflow`).
   - *Lesson*: When refactoring a pervasive cross-cutting dependency, audit all downstream integration and contract tests early. Update workflow container classes (`SelfCorrectiveRagWorkflow`) to accept and pass the injected dependency before pruning legacy hooks.

3. **Strict Code & Markdown Formatter Gates**:
   - *Friction*: Ruff formats Python code snippets located within Markdown files (`docs/spec.md`, `docs/specs/*.md`). Unformatted snippets caused `ruff format --check .` to fail during verification even though application code was clean.
   - *Lesson*: Always execute both `uv run ruff check .` and `uv run ruff format --check .` across the full workspace (including docs) prior to committing, and isolate markdown/workspace formatting changes into dedicated commits.

---

## 5. Codified Agent Guardrails

1. **Encapsulate Prompt CMS Integration Behind Protocol Seams**:
   - Prompt management must remain inside `app/rag/prompts/` adhering to `PromptStore`. Never scatter prompt text or observation dictionary building across domain nodes.
2. **Value Objects Must Defensively Copy Nested State**:
   - Any value object encapsulating execution options or metadata dictionaries must clone nested collections upon copying or derived mutations.
3. **Prefer Dependency Injection Over Monkeypatching**:
   - Tests must never monkeypatch module-level functions or globals. Inject `LocalPromptStore` or use `fake_prompt_store`.
4. **Enforce Formatting Gates on Documentation**:
   - Verify that python snippets inside Markdown specifications follow workspace formatting rules.

---

## 6. Artifact & Ticket Archive Status

All 4 tickets for Milestone 0018 are completed and archived under `.scratch/archive/0018-unified-prompt-store-protocol-seam/issues/`:

| Ticket | Description | Status |
|---|---|---|
| **Ticket 01** | Core PromptStore Seam, RenderedPrompt Model & Local Adapter | Completed & Archived |
| **Ticket 02** | Langfuse CMS Production Adapter, Factory & Test Fixtures | Completed & Archived |
| **Ticket 03** | Dependency Injection in Workflow Engine, Classifiers & Main Startup | Completed & Archived |
| **Ticket 04** | Zero-Shim Pruning of `app/services/prompts.py` & Test Modernization | Completed & Archived |
