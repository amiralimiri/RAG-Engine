# 03: Dependency Injection in Workflow Engine, Classifiers & Main Startup

**What to build:**
Refactor `app/rag/engine.py`, `app/rag/classifiers.py`, and `app/main.py` to consume the `PromptStore` protocol seam via explicit dependency injection. Eliminate procedural `_build_prompt_config` and `is_fallback` inspections across all nodes and classifier adapters, collapsing prompt compilation and observation metadata binding into single `prompt_store.render(...)` calls. Migrate `app/main.py` application startup lifespan to invoke `get_prompt_store().seed_prompts()`.

**Blocked by:** 02: Langfuse CMS Production Adapter, Factory & Test Fixtures

**Status:** closed

- [x] Parameterize `create_agentic_rag_graph` in `app/rag/engine.py` with `prompt_store: PromptStore | None = None` defaulting to `get_prompt_store()`.
- [x] Refactor `node_condense_query`, `direct_answer`, and `_prepare_generation_prompt` in `engine.py` to call `prompt_store.render(...)`.
- [x] Parameterize `OpenAIClassifierAdapter` and `JevClassifierAdapter` in `app/rag/classifiers.py` with `prompt_store: PromptStore | None = None` defaulting to `get_prompt_store()`.
- [x] Refactor `route`, `grade_kb`, and `grade_web` in `OpenAIClassifierAdapter` and `JevClassifierAdapter` to call `prompt_store.render(...)` and pass `rendered.config` into models.
- [x] Remove obsolete procedural `_build_prompt_config` helper from `app/rag/classifiers.py`.
- [x] Update `app/main.py` lifespan to invoke `get_prompt_store().seed_prompts()`.
- [x] Add integration tests in `tests/test_workflow_engine.py` and `tests/test_workflow_classifiers.py` asserting proper execution with custom injected `PromptStore`.
