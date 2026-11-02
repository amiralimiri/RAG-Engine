# 02: Langfuse CMS Production Adapter, Factory & Test Fixtures

**What to build:**
Implement the production `LangfusePromptStore` adapter in `app/rag/prompts/langfuse.py`, connecting to Langfuse Prompt CMS with in-memory TTL caching, automatic fail-open fallback to local templates, and observation metadata linking (`langfuse_prompt`). Implement idempotent startup prompt seeding (`seed_prompts()`). Provide dependency injection lifecycle functions (`get_prompt_store`, `reset_prompt_store`) in `app/rag/prompts/__init__.py`. Integrate prompt store resetting and the `fake_prompt_store` test double fixture into `tests/conftest.py`.

**Blocked by:** 01: Core PromptStore Seam, RenderedPrompt Model & Local Adapter

**Status:** ready-for-agent

- [ ] Implement `LangfusePromptStore` in `app/rag/prompts/langfuse.py` with in-memory TTL caching and fail-open local fallback when Langfuse is disabled or unreachable.
- [ ] Ensure `LangfusePromptStore.render` attaches `langfuse_prompt` to `config["metadata"]` when remote prompt is retrieved, and `prompt_name` + `prompt_fallback: True` when falling back.
- [ ] Implement `LangfusePromptStore.seed_prompts()` to idempotently check existing prompts on the Langfuse server with `cache_ttl_seconds=0` and create missing prompts with label `"production"`.
- [ ] Implement `get_prompt_store(settings=None, store_type=None)` and `reset_prompt_store()` in `app/rag/prompts/__init__.py`.
- [ ] Add `reset_prompt_store()` to the autouse cleanup fixture in `tests/conftest.py` and expose the `fake_prompt_store` fixture injecting `LocalPromptStore`.
- [ ] Add unit tests in `tests/rag/test_langfuse_prompt_store.py` verifying remote retrieval, TTL caching, fail-open error handling, observation metadata generation, and seeding idempotency.
