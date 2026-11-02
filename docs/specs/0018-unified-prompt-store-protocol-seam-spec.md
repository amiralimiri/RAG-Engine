# Technical Specification: Unified Prompt Store Protocol Seam and Zero-Shim Migration

- **ADR Reference:** `docs/adr/0018-unified-prompt-store-protocol-seam.md`
- **Target Subsystem:** `app/rag/prompts/`, `app/rag/engine.py`, `app/rag/classifiers.py`, `app/main.py`
- **Specification Document:** `docs/spec.md` & `docs/specs/0018-unified-prompt-store-protocol-seam-spec.md`
- **Status:** Draft / Ready for Implementation

---

## Problem Statement

Following the introduction of the pluggable knowledge store (ADR-0009), evaluation engine (ADR-0013), and conversation memory subsystem (ADR-0016 / ADR-0017), the prompt management subsystem remains the last unseamed legacy area in the repository.

Currently, prompt retrieval and interpolation are implemented through procedural functions in `app/services/prompts.py` (`get_workflow_prompt`, `seed_prompt`, `seed_all_prompts`) and rely on mutable module-level dictionaries and sets (`LOCAL_PROMPTS`, `_seeded_prompts`). This architecture produces significant friction:

1. **Leaky Fallback and SDK Types**: Callers in `app/rag/engine.py` and `app/rag/classifiers.py` receive either a Langfuse `TextPromptClient` or a local `FallbackPrompt`. Callers must defensively check `getattr(prompt_obj, "is_fallback", False)` and manually assemble `RunnableConfig` metadata dictionaries (`_build_prompt_config`) to link observations in Langfuse traces.
2. **Pervasive Test Monkeypatching**: Because there is no protocol seam or test double for prompts, unit and integration tests must monkeypatch module globals (`app.services.prompts.get_langfuse_client`, `app.services.prompts.get_settings`, `_seeded_prompts`) rather than injecting a fast, in-memory double.
3. **Procedural Startup Seeding**: `app/main.py` imports and executes procedural prompt seeding, entangling the application lifespan with Langfuse SDK network checks and mutable process state.
4. **Architectural Asymmetry**: Unlike knowledge (`app/rag/knowledge/`), memory (`app/rag/memory/`), and evaluation (`app/rag/evaluation/`), prompts are isolated in `app/services/prompts.py`, violating domain cohesion and zero-shim architectural guidelines (ADR-0011).

---

## Solution

Consolidate prompt resolution, template interpolation, observation metadata binding, and startup seeding behind a unified, deep `PromptStore` protocol seam in `app/rag/prompts/`:

1. **Protocol Seam (`PromptStore`)**:
   Expose a high-leverage method `render(name, config=None, **variables) -> RenderedPrompt` that resolves the prompt template, performs fail-open mustache interpolation, and merges Langfuse observation metadata directly into `RunnableConfig`.
2. **Domain Value Object (`RenderedPrompt`)**:
   Return a strongly-typed model encapsulating `name: str`, `text: str`, `config: dict[str, Any]`, and `raw_prompt: Any`, implementing `__str__` returning `self.text` for seamless coercion when passed into chat models or LLMs.
3. **Pluggable Concrete Adapters**:
   - `LangfusePromptStore`: Production adapter with in-memory TTL caching, remote Langfuse Prompt CMS retrieval, automatic fail-open local fallback, and Langfuse observation linking.
   - `LocalPromptStore`: In-memory offline adapter backed by canonical template strings for deterministic unit testing without network or third-party credentials.
4. **Deterministic Template Engine**:
   Provide a shared, fail-open mustache interpolation engine handling variable whitespace, backslash escapes, and missing variable preservation identically across adapters.
5. **Dependency Injection**:
   Inject `prompt_store: PromptStore | None = None` into `create_agentic_rag_graph`, `OpenAIClassifierAdapter`, and `JevClassifierAdapter`, defaulting to `get_prompt_store()`.
6. **Zero-Shim Migration**:
   Update all callers (`engine.py`, `classifiers.py`, `main.py`) directly to `app/rag/prompts/` and prune `app/services/prompts.py` completely per ADR-0011.

---

## User Stories

1. As a workflow engine developer, I want to call `prompt_store.render("rag:direct_answer", config=run_cfg, question=q)`, so that I receive compiled prompt text and pre-merged observation configuration in a single call without manual `RunnableConfig` assembly.
2. As a decision classifier adapter, I want `prompt_store.render(...)` to automatically attach the appropriate observation metadata (`langfuse_prompt` or `prompt_fallback: True`), so that my routing and grading methods do not branch on internal SDK fallback flags.
3. As an engineer writing unit tests, I want `LocalPromptStore` to execute completely offline in memory, so that test suites run deterministically in milliseconds without network dependencies or monkeypatching.
4. As a test author, I want `tests/conftest.py` to automatically reset the prompt store singleton between tests via an autouse fixture, so that test cases never suffer from cross-test state leakage.
5. As a developer building custom workflow variants or test scenarios, I want `LocalPromptStore.register_prompt(name, template)` to register custom prompts dynamically, so that tests can supply specialized prompts without mutating global dictionary state.
6. As an LLM caller passing prompts into LangChain chat models, I want `str(rendered_prompt)` to yield `rendered_prompt.text`, so that models can accept rendered prompts directly without unpack ceremony.
7. As an IT support user submitting questions containing backslashes (e.g. Windows file paths like `C:\Users\foo`), I want the mustache template compiler to preserve backslashes safely, so that path references are never corrupted during prompt interpolation.
8. As a developer extending prompts with variable spacing (e.g. `{{  current_query  }}`), I want the template compiler to trim whitespace inside mustache brackets, so that template whitespace variations do not cause compile failures.
9. As a developer omitting optional context variables during prompt compilation, I want the compiler to preserve unprovided mustache tags fail-open, so that partial formatting does not raise runtime KeyError or regex exceptions.
10. As an ops engineer launching the application, I want `app/main.py` to call `prompt_store.seed_prompts()` during startup lifespan, so that canonical prompts are verified in Langfuse once at boot and live inference runs remain read-only.
11. As a maintainer inspecting the codebase, I want all prompt-related code to reside in `app/rag/prompts/` rather than `app/services/prompts.py`, so that repository navigation conforms to standard domain packaging.
12. As a developer verifying zero-shim standards (ADR-0011), I want `app/services/prompts.py` deleted rather than left behind as a permanent redirect shim, so that the codebase remains free of dead facade layers.

---

## Implementation Decisions

### 1. Subsystem Architecture (`app/rag/prompts/`)
Organize prompt management into a dedicated domain package:
- `app/rag/prompts/base.py`: Declares `PromptStore` protocol seam, `RenderedPrompt` model, and canonical prompt keys constant `CANONICAL_PROMPTS: dict[str, str]`.
- `app/rag/prompts/local.py`: Implements `LocalPromptStore` satisfying `PromptStore`.
- `app/rag/prompts/langfuse.py`: Implements `LangfusePromptStore` satisfying `PromptStore`.
- `app/rag/prompts/__init__.py`: Exports public symbols, `get_prompt_store()`, and `reset_prompt_store()`.

### 2. Seam Interface Contract
The `PromptStore` protocol seam is defined as:
```python
@runtime_checkable
class PromptStore(Protocol):
    def render(
        self,
        name: str,
        config: RunnableConfig | None = None,
        **variables: Any,
    ) -> RenderedPrompt: ...

    def get_template(self, name: str) -> str: ...

    def seed_prompts(self) -> dict[str, bool]: ...
```

### 3. Value Object Contract (`RenderedPrompt`)
```python
class RenderedPrompt(BaseModel):
    name: str
    text: str
    config: dict[str, Any] = Field(default_factory=dict)
    raw_prompt: Any = None

    def __str__(self) -> str:
        return self.text
```
The `config` dictionary is pre-merged with inbound `RunnableConfig` (if provided) and populated with observation metadata:
- If backed by Langfuse `TextPromptClient`: `config["metadata"]["langfuse_prompt"] = raw_prompt`
- If backed by local fallback template: `config["metadata"]["prompt_name"] = name` and `config["metadata"]["prompt_fallback"] = True`

### 4. Template Interpolation Rules
Deterministic mustache compilation:
- Replace `r"\{\{\s*" + re.escape(key) + r"\s*\}\}"` with `str(value)` using a callable replacer to prevent backslash escaping pitfalls.
- Leave unprovided placeholders untouched.
- Maintain identical behavior across `LocalPromptStore` and `LangfusePromptStore`.

### 5. Concrete Adapters
- **`LocalPromptStore`**:
  - Initialized with `templates: dict[str, str] | None = None`, defaulting to a copy of `CANONICAL_PROMPTS`.
  - Exposes `register_prompt(name: str, template: str)` for dynamic runtime and test registration.
  - `seed_prompts()` returns `{name: True for name in self._templates}` without network operations.
- **`LangfusePromptStore`**:
  - Parameterized with `settings: Settings | None = None` and `client: Any | None = None`.
  - Retrieves remote prompt with `client.get_prompt(name=name, label="production", cache_ttl_seconds=ttl)`.
  - Falls back gracefully to `LocalPromptStore` logic when `langfuse_enabled` is False, client is unavailable, or remote call raises an exception.
  - `seed_prompts()` iterates over `CANONICAL_PROMPTS`, checks existing prompts on server with `cache_ttl_seconds=0`, and creates missing prompts with label `"production"`.

### 6. Dependency Injection & Caller Updates
- `create_agentic_rag_graph` in `app/rag/engine.py` accepts `prompt_store: PromptStore | None = None` defaulting to `get_prompt_store()`.
- `OpenAIClassifierAdapter` and `JevClassifierAdapter` in `app/rag/classifiers.py` accept `prompt_store: PromptStore | None = None` defaulting to `get_prompt_store()`.
- Remove `_build_prompt_config` from `app/rag/classifiers.py` and delete `FallbackPrompt` from `app/services/prompts.py`.
- Callers replace `get_workflow_prompt` + `_build_prompt_config` with a single `prompt_store.render(name, config=config, **variables)`.
- `app/main.py` updates startup lifespan to call `get_prompt_store().seed_prompts()`.
- Delete `app/services/prompts.py` completely.

### 7. Test Suite Infrastructure
- `tests/conftest.py` adds `reset_prompt_store()` inside the autouse fixture.
- Expose `fake_prompt_store` fixture providing an isolated `LocalPromptStore` instance.
- Update `tests/test_prompts_cms.py` to test `LocalPromptStore`, `LangfusePromptStore`, and the `PromptStore` protocol.

---

## Testing Decisions

### What Makes a Good Test
- **Behavior-Driven Over Implementation**: Tests assert against public protocol methods (`render`, `get_template`, `seed_prompts`), verifying compiled string output, metadata propagation in `RunnableConfig`, and fallback handling.
- **Offline Speed**: Unit tests must not require Langfuse API keys or network access; `LocalPromptStore` must execute in under 1ms.
- **Integration Seam Tests**: Tests in `tests/test_workflow_engine.py` and `tests/test_workflow_classifiers.py` must verify that injecting a custom `PromptStore` correctly influences node behavior and classifier output.

### Target Test Modules
1. `tests/rag/test_prompt_store.py`: Protocol verification, template interpolation edge cases (whitespace, backslashes, missing variables), dynamic prompt registration, and singleton lifecycle.
2. `tests/rag/test_langfuse_prompt_store.py`: Remote caching, TTL behavior, fail-open fallback when Langfuse is disabled or errors, observation metadata synthesis, and seeding idempotency.
3. `tests/test_workflow_engine.py`: Graph execution with custom injected `PromptStore`.
4. `tests/test_workflow_classifiers.py`: Classifiers (`OpenAIClassifierAdapter`, `JevClassifierAdapter`) routing and grading with injected `PromptStore`.

### Prior Art
- `tests/rag/test_knowledge_store.py` (offline `InMemoryVectorStoreAdapter` tests)
- `tests/rag/test_evaluation_engine.py` (offline `FakeEvaluator` tests)
- `tests/rag/test_conversation_memory_in_memory.py` (offline `InMemoryConversationStore` tests)

---

## Out of Scope

- Migrating prompts to external template files (Jinja2 / disk files); templates remain canonical Python dictionary constants in `base.py`.
- Modifying prompt texts or system instructions; all 11 existing canonical prompt strings remain unaltered.
- Changing `WorkflowRunner` execution pipeline or streaming event semantics.
- Introducing multi-lingual or localized prompt variants.

---

## Further Notes

- All 11 canonical prompt keys must be preserved identically:
  - `rag:router:llm`
  - `rag:router:jev`
  - `rag:grade_kb:llm`
  - `rag:grade_kb:jev`
  - `rag:grade_web:llm`
  - `rag:grade_web:jev`
  - `rag:rewrite_query`
  - `rag:generate_kb`
  - `rag:generate_web`
  - `rag:direct_answer`
  - `rag:condense_query`
