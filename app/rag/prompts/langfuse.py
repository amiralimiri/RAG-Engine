"""Production Langfuse Prompt CMS adapter with in-memory TTL caching and fail-open fallback."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client
from app.rag.prompts.base import CANONICAL_PROMPTS, RenderedPrompt, interpolate_mustache
from app.rag.prompts.local import LocalPromptStore

if TYPE_CHECKING:
    from langfuse import Langfuse

    from app.core.config import Settings

logger = logging.getLogger(__name__)


class LangfusePromptStore:
    """Production Langfuse Prompt CMS adapter with in-memory TTL caching and fail-open local fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Langfuse | Any | None = None,
        cache_ttl_seconds: int | None = None,
        local_templates: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client_override = client
        if cache_ttl_seconds is not None:
            self._cache_ttl_seconds = cache_ttl_seconds
        else:
            self._cache_ttl_seconds = getattr(self._settings, "langfuse_prompt_cache_ttl_seconds", 300)

        self._local_store = LocalPromptStore(templates=local_templates)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._seeded_prompts: set[str] = set()

    def _get_client(self) -> Any | None:
        """Resolve Langfuse client instance or return override."""
        if self._client_override is not None:
            return self._client_override
        if not getattr(self._settings, "langfuse_enabled", True):
            return None
        try:
            return get_langfuse_client(self._settings)
        except TypeError:
            return get_langfuse_client()

    def _fetch_remote_prompt(self, name: str) -> Any | None:
        """Fetch remote prompt from Langfuse Prompt CMS or TTL cache, returning None on failure."""
        now = time.monotonic()
        if name in self._cache:
            expires_at, cached_obj = self._cache[name]
            if now < expires_at:
                return cached_obj
            self._cache.pop(name, None)

        if not getattr(self._settings, "langfuse_enabled", True):
            return None

        client = self._get_client()
        if client is None or not hasattr(client, "get_prompt"):
            return None

        try:
            try:
                prompt_obj = client.get_prompt(
                    name=name,
                    label="production",
                    cache_ttl_seconds=self._cache_ttl_seconds,
                )
            except TypeError:
                prompt_obj = client.get_prompt(
                    name=name,
                    cache_ttl_seconds=self._cache_ttl_seconds,
                )

            if prompt_obj is not None and not getattr(prompt_obj, "is_fallback", False):
                if self._cache_ttl_seconds > 0:
                    self._cache[name] = (time.monotonic() + self._cache_ttl_seconds, prompt_obj)
                return prompt_obj
            return None
        except Exception as exc:
            logger.warning(
                "Failed to fetch prompt '%s' from Langfuse Prompt CMS: %s. Using local fallback.",
                name,
                exc,
            )
            return None

    def get_template(self, name: str) -> str:
        """Retrieve raw template string by prompt name."""
        prompt_obj = self._fetch_remote_prompt(name)
        if prompt_obj is not None:
            template = getattr(prompt_obj, "prompt", getattr(prompt_obj, "template", None))
            if isinstance(template, str):
                return template
        return self._local_store.get_template(name)

    def render(
        self,
        name: str,
        config: RunnableConfig | None = None,
        **variables: Any,
    ) -> RenderedPrompt:
        """Compile prompt template and return RenderedPrompt with observation config."""
        prompt_obj = self._fetch_remote_prompt(name)
        if prompt_obj is not None:
            template = getattr(prompt_obj, "prompt", getattr(prompt_obj, "template", str(prompt_obj)))
            compiled_text = interpolate_mustache(template, **variables)

            cfg: dict[str, Any] = dict(config) if config else {}
            metadata = dict(cfg.get("metadata") or {})
            metadata["langfuse_prompt"] = prompt_obj
            cfg["metadata"] = metadata

            return RenderedPrompt(
                name=name,
                text=compiled_text,
                config=cfg,
                raw_prompt=prompt_obj,
            )

        # Fail-open local template fallback
        return self._local_store.render(name, config=config, **variables)

    def _seed_prompt(self, name: str) -> bool:
        """Safely seed a canonical prompt into Langfuse Prompt CMS once.

        Checks if the prompt already exists on the Langfuse server with label 'production'
        before calling create_prompt to prevent duplicate prompt versions.
        """
        if name in self._seeded_prompts:
            return True
        if not getattr(self._settings, "langfuse_enabled", True):
            return False
        client = self._get_client()
        if not client:
            return False
        try:
            fallback_text = self._local_store.get_template(name)
        except KeyError:
            return False

        # 1. Check if the prompt already exists on the Langfuse server without fallback
        if hasattr(client, "get_prompt"):
            try:
                existing = client.get_prompt(
                    name=name,
                    label="production",
                    cache_ttl_seconds=0,
                )
                if existing is not None and not getattr(existing, "is_fallback", False):
                    self._seeded_prompts.add(name)
                    logger.debug(
                        "Prompt '%s' already exists in Langfuse Prompt CMS (version %s); skipping create_prompt",
                        name,
                        getattr(existing, "version", "unknown"),
                    )
                    return True
            except Exception:
                # Prompt does not exist on Langfuse server; proceed to create
                pass

        # 2. Only call create_prompt if prompt does not exist on the server
        if not hasattr(client, "create_prompt"):
            return False

        try:
            client.create_prompt(
                name=name,
                prompt=fallback_text,
                labels=["production"],
                type="text",
            )
            self._seeded_prompts.add(name)
            logger.info("Auto-seeded prompt '%s' into Langfuse Prompt CMS with label 'production'", name)
            return True
        except Exception as exc:
            logger.warning("Could not auto-seed prompt '%s' into Langfuse: %s", name, exc)
            return False

    def seed_prompts(self) -> dict[str, bool]:
        """Idempotently seed canonical prompts into Langfuse Prompt CMS if not already present."""
        prompt_names = list(CANONICAL_PROMPTS.keys())
        if not getattr(self._settings, "langfuse_enabled", True):
            return {name: False for name in prompt_names}
        client = self._get_client()
        if not client or not hasattr(client, "create_prompt"):
            return {name: False for name in prompt_names}
        return {name: self._seed_prompt(name) for name in prompt_names}
