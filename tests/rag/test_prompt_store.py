from typing import Any

import pytest


def test_rendered_prompt_value_object():
    """Verify RenderedPrompt fields, defaults, and __str__ coercion."""
    from app.rag.prompts.base import RenderedPrompt

    prompt = RenderedPrompt(name="test_prompt", text="Hello world")
    assert prompt.name == "test_prompt"
    assert prompt.text == "Hello world"
    assert prompt.config == {}
    assert prompt.raw_prompt is None
    assert str(prompt) == "Hello world"
    assert f"{prompt}" == "Hello world"


def test_rendered_prompt_with_config_and_raw_prompt():
    """Verify RenderedPrompt preserves custom config and raw_prompt references."""
    from app.rag.prompts.base import RenderedPrompt

    custom_cfg = {"metadata": {"source": "test"}, "tags": ["unit"]}
    raw_obj = object()
    prompt = RenderedPrompt(
        name="custom",
        text="Sample text",
        config=custom_cfg,
        raw_prompt=raw_obj,
    )
    assert prompt.name == "custom"
    assert prompt.text == "Sample text"
    assert prompt.config == custom_cfg
    assert prompt.raw_prompt is raw_obj
    assert str(prompt) == "Sample text"


def test_interpolate_mustache_basic():
    """Verify basic mustache variable replacement."""
    from app.rag.prompts.base import interpolate_mustache

    tpl = "Hello {{name}}, welcome to {{place}}!"
    res = interpolate_mustache(tpl, name="Alice", place="Wonderland")
    assert res == "Hello Alice, welcome to Wonderland!"


def test_interpolate_mustache_whitespace_inside_brackets():
    """Verify whitespace inside tag brackets is trimmed and matched."""
    from app.rag.prompts.base import interpolate_mustache

    tpl = "Question: {{ question }}\nQuery: {{\tcurrent_query\t}}\nContext: {{\n context \n}}"
    res = interpolate_mustache(
        tpl,
        question="How do I reset VPN?",
        current_query="vpn reset",
        context="VPN instructions",
    )
    assert res == "Question: How do I reset VPN?\nQuery: vpn reset\nContext: VPN instructions"


def test_interpolate_mustache_preserves_backslashes():
    """Verify backslashes in values (e.g. Windows file paths) are not escaped or lost."""
    from app.rag.prompts.base import interpolate_mustache

    tpl = "Path: {{filepath}}"
    path_val = r"C:\Users\admin\Desktop\new_folder\test.txt"
    res = interpolate_mustache(tpl, filepath=path_val)
    assert res == f"Path: {path_val}"


def test_interpolate_mustache_preserves_unprovided_tags():
    """Verify unprovided tags remain intact fail-open."""
    from app.rag.prompts.base import interpolate_mustache

    tpl = "User: {{user}}, Missing: {{missing_var}}, Role: {{role}}"
    res = interpolate_mustache(tpl, user="Alice")
    assert res == "User: Alice, Missing: {{missing_var}}, Role: {{role}}"


def test_interpolate_mustache_converts_non_string_values():
    """Verify non-string values (numbers, booleans) are safely converted via str()."""
    from app.rag.prompts.base import interpolate_mustache

    tpl = "Count: {{count}}, Valid: {{is_valid}}"
    res = interpolate_mustache(tpl, count=42, is_valid=True)
    assert res == "Count: 42, Valid: True"


def test_canonical_prompts_contains_all_11_templates():
    """Verify CANONICAL_PROMPTS dictionary contains all required prompt keys."""
    from app.rag.prompts.base import CANONICAL_PROMPTS

    expected_keys = {
        "rag:router:llm",
        "rag:router:jev",
        "rag:grade_kb:llm",
        "rag:grade_kb:jev",
        "rag:grade_web:llm",
        "rag:grade_web:jev",
        "rag:rewrite_query",
        "rag:generate_kb",
        "rag:generate_web",
        "rag:direct_answer",
        "rag:condense_query",
    }
    assert set(CANONICAL_PROMPTS.keys()) == expected_keys
    for key, template in CANONICAL_PROMPTS.items():
        assert isinstance(template, str)
        assert len(template.strip()) > 0


def test_prompt_store_protocol_conformance():
    """Verify runtime checkability of PromptStore protocol."""
    from langchain_core.runnables import RunnableConfig

    from app.rag.prompts.base import PromptStore, RenderedPrompt

    class ConformingStore:
        def render(
            self,
            name: str,
            config: RunnableConfig | None = None,
            **variables: Any,
        ) -> RenderedPrompt:
            return RenderedPrompt(name=name, text="dummy")

        def get_template(self, name: str) -> str:
            return "dummy template"

        def seed_prompts(self) -> dict[str, bool]:
            return {"dummy": True}

    class NonConformingStore:
        pass

    assert isinstance(ConformingStore(), PromptStore)
    assert not isinstance(NonConformingStore(), PromptStore)


def test_local_prompt_store_satisfies_protocol():
    """Verify LocalPromptStore satisfies the PromptStore protocol."""
    from app.rag.prompts.base import PromptStore
    from app.rag.prompts.local import LocalPromptStore

    store = LocalPromptStore()
    assert isinstance(store, PromptStore)


def test_local_prompt_store_default_templates_and_get():
    """Verify LocalPromptStore initializes with canonical templates."""
    from app.rag.prompts.base import CANONICAL_PROMPTS
    from app.rag.prompts.local import LocalPromptStore

    store = LocalPromptStore()
    tpl = store.get_template("rag:direct_answer")
    assert tpl == CANONICAL_PROMPTS["rag:direct_answer"]

    with pytest.raises(KeyError, match="not found"):
        store.get_template("nonexistent:prompt")


def test_local_prompt_store_custom_templates_and_register():
    """Verify LocalPromptStore accepts custom templates and supports dynamic registration."""
    from app.rag.prompts.local import LocalPromptStore

    store = LocalPromptStore(templates={"custom:one": "Template 1: {{var}}"})
    assert store.get_template("custom:one") == "Template 1: {{var}}"

    with pytest.raises(KeyError):
        store.get_template("rag:direct_answer")

    store.register_prompt("custom:two", "Template 2: {{val}}")
    assert store.get_template("custom:two") == "Template 2: {{val}}"


def test_local_prompt_store_render_basic_and_metadata():
    """Verify LocalPromptStore.render produces RenderedPrompt with fallback metadata."""
    from app.rag.prompts.local import LocalPromptStore

    store = LocalPromptStore()
    rendered = store.render("rag:direct_answer", question="How do I change my password?")

    assert rendered.name == "rag:direct_answer"
    assert (
        rendered.text
        == "Respond briefly, warmly, and naturally as an enterprise IT support copilot to: How do I change my password?"
    )
    assert rendered.config == {
        "metadata": {
            "prompt_name": "rag:direct_answer",
            "prompt_fallback": True,
        }
    }
    assert rendered.raw_prompt is None
    assert str(rendered) == rendered.text


def test_local_prompt_store_render_merges_inbound_config():
    """Verify LocalPromptStore.render merges inbound RunnableConfig with fallback metadata."""
    from app.rag.prompts.local import LocalPromptStore

    store = LocalPromptStore()
    inbound_config = {
        "metadata": {"session_id": "ses-123", "user": "alice"},
        "tags": ["prod", "agent"],
        "run_name": "test_run",
    }
    rendered = store.render(
        "rag:direct_answer",
        config=inbound_config,
        question="Hello!",
    )

    assert rendered.config["tags"] == ["prod", "agent"]
    assert rendered.config["run_name"] == "test_run"
    assert rendered.config["metadata"] == {
        "session_id": "ses-123",
        "user": "alice",
        "prompt_name": "rag:direct_answer",
        "prompt_fallback": True,
    }


def test_local_prompt_store_seed_prompts():
    """Verify LocalPromptStore.seed_prompts reports all templates as seeded."""
    from app.rag.prompts.local import LocalPromptStore

    store = LocalPromptStore(templates={"p1": "text1", "p2": "text2"})
    result = store.seed_prompts()
    assert result == {"p1": True, "p2": True}


def test_package_exports():
    """Verify app.rag.prompts exports core domain symbols."""
    import app.rag.prompts as prompts

    assert hasattr(prompts, "PromptStore")
    assert hasattr(prompts, "LocalPromptStore")
    assert hasattr(prompts, "RenderedPrompt")
    assert hasattr(prompts, "CANONICAL_PROMPTS")
    assert hasattr(prompts, "interpolate_mustache")
