"""In-memory deterministic prompt store adapter for offline testing and local execution."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.rag.prompts.base import CANONICAL_PROMPTS, RenderedPrompt, interpolate_mustache


class LocalPromptStore:
    """Deterministic in-memory prompt store backed by template dictionary."""

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self._templates: dict[str, str] = dict(templates) if templates is not None else dict(CANONICAL_PROMPTS)

    def get_template(self, name: str) -> str:
        """Retrieve raw template string by prompt name."""
        if name not in self._templates:
            raise KeyError(f"Prompt template '{name}' not found")
        return self._templates[name]

    def register_prompt(self, name: str, template: str) -> None:
        """Register or override a prompt template dynamically."""
        self._templates[name] = template

    def render(
        self,
        name: str,
        config: RunnableConfig | None = None,
        **variables: Any,
    ) -> RenderedPrompt:
        """Compile prompt template and return RenderedPrompt with fallback observation config."""
        template = self.get_template(name)
        compiled_text = interpolate_mustache(template, **variables)

        cfg: dict[str, Any] = dict(config) if config else {}
        metadata = dict(cfg.get("metadata") or {})
        metadata.update(
            {
                "prompt_name": name,
                "prompt_fallback": True,
            }
        )
        cfg["metadata"] = metadata

        return RenderedPrompt(
            name=name,
            text=compiled_text,
            config=cfg,
            raw_prompt=None,
        )

    def seed_prompts(self) -> dict[str, bool]:
        """Mark all registered templates as seeded without external network access."""
        return {name: True for name in self._templates}
