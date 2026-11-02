# Prompt Store Protocol Seam Patterns

Guidelines and architecture patterns established in ADR-0018 (Unified Prompt Store Protocol Seam and Zero-Shim Migration) for managing prompt templates, dynamic interpolation, Langfuse CMS integration, and observation metadata binding.

---

## 1. Core Principles

1. **Protocol Seam Decoupling (`PromptStore`)**:
   - The prompt management subsystem is located under `app/rag/prompts/` (not in legacy service locations like `app/services/prompts.py`).
   - All prompt retrieval, template compilation, and observation binding must occur through the `PromptStore` protocol seam declared in `app/rag/prompts/base.py`.
   - Never expose or rely on loose module-level prompt dictionaries (`LOCAL_PROMPTS`) or procedural fetching functions (`get_workflow_prompt`).

2. **High-Leverage Value Object (`RenderedPrompt`)**:
   - Compiling a prompt template yields an immutable `RenderedPrompt` domain model containing:
     - `name: str`: Canonical prompt identifier.
     - `text: str`: Rendered prompt text with variables interpolated.
     - `config: dict[str, Any]`: Pre-configured `RunnableConfig` dictionary with observation metadata.
     - `raw_prompt: Any`: Backing prompt client (e.g. `TextPromptClient`) if retrieved from remote CMS.
   - `RenderedPrompt` implements `__str__` returning `self.text`, allowing direct string coercion.

3. **Defensive Cloning of Nested Observation Configs**:
   - In `RenderedPrompt`, shallow copying (`dict(self.config)`) is unsafe when `config` contains nested structures (e.g. `metadata`, `tags`, `callbacks`).
   - Mutations by downstream callers or consecutive nodes can leak or mutate shared observation tags.
   - Always perform a defensive clone (`_clone_config()`) when deriving or copying `RenderedPrompt` instances (e.g. in `.with_text()`):
     ```python
     def _clone_config(self) -> dict[str, Any]:
         cloned = dict(self.config)
         if "metadata" in cloned and isinstance(cloned["metadata"], dict):
             cloned["metadata"] = dict(cloned["metadata"])
         if "tags" in cloned and isinstance(cloned["tags"], list):
             cloned["tags"] = list(cloned["tags"])
         if "callbacks" in cloned and isinstance(cloned["callbacks"], list):
             cloned["callbacks"] = list(cloned["callbacks"])
         return cloned
     ```

4. **Fail-Open Mustache Template Interpolation**:
   - Templates use standard mustache-style delimiters (`{{variable}}`).
   - The template engine must be resilient to arbitrary whitespace inside brackets (e.g. `{{  variable  }}`), handle backslashes in values safely without regex escape corruption, and preserve missing/unprovided variables verbatim rather than crashing.

5. **Explicit Dependency Injection Across Engine & Classifiers**:
   - Node functions (`node_condense_query`, `direct_answer`, `_prepare_generation_prompt`) and classifier adapters (`OpenAIClassifierAdapter`, `JevClassifierAdapter`) must accept `prompt_store: PromptStore | None = None` and default to `get_prompt_store()`.
   - Workflows (such as `SelfCorrectiveRagWorkflow`) must accept and forward `prompt_store` to graph factory functions and expose a `prompt_store` property.

6. **Zero-Shim Pruning**:
   - `app/services/prompts.py` is permanently deleted. No compatibility wrappers, re-exports, or forwarders are maintained.
   - All callers import directly from `app.rag.prompts`.

---

## 2. Seam Protocol Contract (`app/rag/prompts/base.py`)

```python
@runtime_checkable
class PromptStore(Protocol):
    """Protocol seam for template resolution, compilation, and observation binding."""

    def render(
        self,
        name: str,
        config: RunnableConfig | None = None,
        **variables: Any,
    ) -> RenderedPrompt: ...

    def get_template(self, name: str) -> str: ...

    def seed_prompts(self) -> dict[str, bool]: ...
```

---

## 3. Test Doubles & Fixtures

1. **Deterministic Test Double (`LocalPromptStore`)**:
   - Unit tests must use `LocalPromptStore` (`app/rag/prompts/local.py`) to run 100% offline with 0ms network latency.
   - Custom templates can be injected via constructor or registered dynamically:
     ```python
     store = LocalPromptStore(templates={"rag:router:llm": "Custom router template: {{question}}"})
     store.register_prompt("rag:custom", "Hello {{name}}")
     ```

2. **Standard Pytest Fixture (`fake_prompt_store`)**:
   - `tests/conftest.py` provides the `fake_prompt_store` fixture injecting a clean `LocalPromptStore` and resetting prompt store state via `reset_prompt_store()`.
   - Never monkeypatch global functions like `get_workflow_prompt`. Inject `fake_prompt_store` into workflow containers or pass `prompt_store=custom_store` directly.

3. **Keeping Downstream Tests Green During Refactoring**:
   - When modifying node signatures or factory signatures, audit all downstream integration tests for mock assertions expecting specific parameter counts or keyword arguments.
   - When migrating away from monkeypatching global functions, update test fixtures to supply mock stores or local stores at the seam boundary rather than leaving legacy patches in place.

---

## 4. Developer Hygiene & Formatting Gates

1. **Check Formatter on Markdown Codeblocks**:
   - Ruff formats python code blocks inside Markdown files (`docs/**/*.md`).
   - Always run `uv run ruff format --check .` and `uv run ruff check .` before finalizing commits.
2. **Isolate Formatting Changes**:
   - Keep architectural commits focused on substantive logic changes. Run broad repository formatting passes in isolated style commits (`style: format workspace`).
