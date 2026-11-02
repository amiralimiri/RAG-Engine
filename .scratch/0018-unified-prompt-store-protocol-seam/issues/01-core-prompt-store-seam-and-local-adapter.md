# 01: Core PromptStore Seam, RenderedPrompt Model & Local Adapter

**What to build:**
Establish the foundational prompt management seam by creating `app/rag/prompts/base.py` and `app/rag/prompts/local.py`. Expose the `PromptStore` protocol seam, the canonical prompt template dictionary (`CANONICAL_PROMPTS`), the `RenderedPrompt` domain value object with `__str__` coercion, and a shared, fail-open mustache template engine. Implement `LocalPromptStore` as a fast, in-memory, deterministic adapter capable of custom template registration and offline prompt rendering.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Create `app/rag/prompts/base.py` declaring `PromptStore` protocol seam (`render`, `get_template`, `seed_prompts`) and `CANONICAL_PROMPTS` containing all 11 canonical prompt templates.
- [ ] Define `RenderedPrompt(BaseModel)` exposing `name: str`, `text: str`, `config: dict[str, Any]`, and `raw_prompt: Any`, implementing `__str__` returning `self.text`.
- [ ] Implement shared deterministic mustache interpolation handling whitespace inside tag brackets (e.g. `{{  var  }}`), backslash escapes in values, and unprovided variable preservation.
- [ ] Implement `LocalPromptStore` in `app/rag/prompts/local.py` accepting optional `templates: dict[str, str]` and exposing `register_prompt(name, template)`.
- [ ] Ensure `LocalPromptStore.render` produces `RenderedPrompt` with `config["metadata"] = {"prompt_name": name, "prompt_fallback": True}` merged with any provided `RunnableConfig`.
- [ ] Add comprehensive unit tests in `tests/rag/test_prompt_store.py` verifying protocol compliance, template interpolation edge cases, and in-memory rendering.
