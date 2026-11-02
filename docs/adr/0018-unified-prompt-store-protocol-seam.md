# ADR 0018: Unified Prompt Store Protocol Seam and Zero-Shim Migration

## Status
Accepted (Extends ADR 0006, ADR 0010, ADR 0011, and ADR 0013)

## Context
Following ADR-0006 (Unified Agentic RAG Engine), ADR-0009 (Knowledge Store Seam), ADR-0013 (Unified Evaluator Seam), and ADR-0016 / ADR-0017 (Conversation Memory Seam), all major subsystems in the repository have been elevated behind cohesive protocol seams with swappable production and in-memory test adapters.

However, prompt management remained in a legacy state within `app/services/prompts.py`:
1. **Lack of a Protocol Seam**: Prompt fetching relied on loose, procedural functions (`get_workflow_prompt`, `seed_prompt`, `seed_all_prompts`) and mutable global state (`LOCAL_PROMPTS` dictionary, `_seeded_prompts` set).
2. **Observation Configuration & Fallback Leakage**: Callers across `app/rag/engine.py` and `app/rag/classifiers.py` had to manually inspect whether returned prompts were `FallbackPrompt` or `TextPromptClient` instances (e.g. checking `getattr(prompt_obj, "is_fallback", False)`) and run duplicate metadata-building logic (`_build_prompt_config`) to bind Langfuse observation parameters.
3. **Pervasive Test Monkeypatching**: Unit and integration tests were forced to patch module-level functions in `app.services.prompts` and manipulate global caches rather than injecting an isolated offline test double.
4. **Violation of Domain Structure**: While knowledge, evaluation, memory, workflows, and tools are located under `app/rag/`, prompt management remained lingering in `app/services/prompts.py`.

To enforce strict modularity, test isolation, caller leverage, and zero-shim architecture, prompt resolution, template interpolation, and observation metadata binding must be consolidated behind a unified `PromptStore` protocol seam in `app/rag/prompts/`.

---

## Decisions

### 1. Unified `app/rag/prompts/` Domain Subsystem
Elevate prompt management into a first-class RAG domain subsystem in `app/rag/prompts/`:
- `app/rag/prompts/base.py`: Protocol seam (`PromptStore`), domain models (`RenderedPrompt`), and canonical prompt identifiers.
- `app/rag/prompts/local.py`: In-memory `LocalPromptStore` adapter for fast, offline, deterministic testing.
- `app/rag/prompts/langfuse.py`: Production `LangfusePromptStore` adapter with TTL caching, automatic fail-open local fallback, and Langfuse observation linking.
- `app/rag/prompts/__init__.py`: Public package exports and dependency injection factory (`get_prompt_store`, `reset_prompt_store`).

### 2. High-Leverage `PromptStore` Protocol Seam
Define a strongly-typed `PromptStore` protocol in `app/rag/prompts/base.py`:
```python
@runtime_checkable
class PromptStore(Protocol):
    """Protocol seam for template resolution, compilation, and observation binding."""

    def render(
        self,
        name: str,
        config: RunnableConfig | None = None,
        **variables: Any,
    ) -> RenderedPrompt:
        """Compile prompt template and return RenderedPrompt with merged observation config."""
        ...

    def get_template(self, name: str) -> str:
        """Retrieve raw template string by prompt name."""
        ...

    def seed_prompts(self) -> dict[str, bool]:
        """Idempotently seed canonical prompts into backing CMS if supported."""
        ...
```

### 3. High-Leverage Domain Model (`RenderedPrompt`)
Define `RenderedPrompt` to encapsulate prompt compilation and observation configuration in a single value object:
```python
class RenderedPrompt(BaseModel):
    name: str
    text: str
    config: dict[str, Any] = Field(default_factory=dict)
    raw_prompt: Any = None

    def __str__(self) -> str:
        """Allow seamless string coercion when passing prompt into LLMs."""
        return self.text
```
- Exposes `__str__` returning `self.text`, allowing direct passage to LLMs (`model.ainvoke(prompt, config=prompt.config)`).
- Pre-merges Langfuse observation metadata (`langfuse_prompt` or `prompt_name` fallback) directly into `config["metadata"]`.

### 4. Deterministic Fail-Open Template Engine
Both `LocalPromptStore` and `LangfusePromptStore` share a deterministic mustache template interpolation engine:
- Trims whitespace inside mustache brackets (e.g. `{{  current_query  }}`).
- Safely handles backslashes in variable values.
- Leaves unprovided placeholders intact (fail-open) rather than throwing runtime errors.

### 5. Swappable Adapters
1. **`LangfusePromptStore`**:
   - Queries Langfuse Prompt CMS via `client.get_prompt(name, label="production", cache_ttl_seconds=...)`.
   - Maintains in-memory TTL cache to minimize network round-trips.
   - Automatically falls back to canonical local template on network failure or when Langfuse is disabled.
   - Binds `langfuse_prompt` object to `config["metadata"]` when remote prompt is used, or `prompt_fallback: True` when local template is used.
2. **`LocalPromptStore`**:
   - Backed entirely by in-memory template dictionary with zero external network or SDK dependencies.
   - Initialized with canonical RAG templates by default.
   - Supports `register_prompt(name: str, template: str)` for test cases and dynamic workflow extensions.
   - Binds `prompt_name` and `prompt_fallback: True` to `config["metadata"]`.

### 6. Dependency Injection & Test Isolation
- Parameterize `create_agentic_rag_graph`, `OpenAIClassifierAdapter`, and `JevClassifierAdapter` with an optional `prompt_store: PromptStore | None = None` parameter defaulting to `get_prompt_store()`.
- Expose `get_prompt_store(settings=None, store_type=None)` and `reset_prompt_store()` in `app/rag/prompts/`.
- Add `reset_prompt_store()` to the autouse cleanup fixture in `tests/conftest.py`.
- Provide a `fake_prompt_store` fixture injecting a clean `LocalPromptStore`.

### 7. Startup Seeding
- In `app/main.py`, replace procedural `seed_all_prompts()` with `get_prompt_store().seed_prompts()` during lifespan/startup.
- Runtime inference calls perform zero writes or remote check queries.

### 8. Zero-Shim Migration (ADR-0011 Compliance)
- Update all callers (`app/rag/engine.py`, `app/rag/classifiers.py`, `app/main.py`) to consume `app/rag/prompts/` directly.
- Delete `app/services/prompts.py` completely without intermediate shims.
- Migrate `tests/test_prompts_cms.py` to test `PromptStore`, `LocalPromptStore`, and `LangfusePromptStore`.

---

## Consequences

### Positive
- **Strict Locality**: Prompt resolution, local template fallbacks, caching, and Langfuse observation configuration concentrate within `app/rag/prompts/`.
- **Maximum Caller Leverage**: Callers obtain compiled text and pre-merged `RunnableConfig` in a single `prompt_store.render(...)` call.
- **Seam Cleanliness**: Eliminates `_build_prompt_config`, `is_fallback` duck-typing, and procedural global state across engine nodes and classifiers.
- **Fast Deterministic Testing**: Unit tests execute completely offline against `LocalPromptStore` without mocking or monkeypatching module globals.
- **Architectural Symmetry**: Brings prompt management into architectural alignment with Knowledge Store, Evaluation Engine, and Conversation Memory.

### Negative / Trade-Offs
- Existing custom workflow plugins or scripts referencing `app.services.prompts` directly must update their imports to `app.rag.prompts`.
