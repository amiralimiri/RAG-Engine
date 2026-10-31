"""Lazy judge model provider for DeepEval LLM and TypeSafe models."""

from __future__ import annotations

import os
import threading
from typing import Any

from app.core.config import Settings, get_settings


class JudgeModels:
    """Thread-safe, lazy-initialized DeepEval judge model container."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._llm: Any = None
        self._jev: Any = None
        self._lock = threading.Lock()

    @property
    def llm(self) -> Any:
        """Lazily initialize and return the LocalModel LLM judge."""
        if self._llm is None:
            with self._lock:
                if self._llm is None:
                    from deepeval.models import LocalModel

                    self._llm = LocalModel(
                        model=self.settings.llm_model,
                        base_url=self.settings.llm_base_url,
                        api_key=self.settings.llm_api_key.get_secret_value(),
                    )
        return self._llm

    @property
    def jev(self) -> Any:
        """Lazily initialize and return the TypeSafeModel JEV judge."""
        if self._jev is None:
            with self._lock:
                if self._jev is None:
                    from deepeval.models import TypeSafeModel

                    if hasattr(self.settings, "jev_base_url") and self.settings.jev_base_url:
                        os.environ["TYPESAFE_BASE_URL"] = self.settings.jev_base_url

                    self._jev = TypeSafeModel(
                        model=self.settings.jev_model,
                        api_key=self.settings.jev_api_key.get_secret_value(),
                    )
        return self._jev


_judge_instance: JudgeModels | None = None
_judge_lock = threading.Lock()


def get_judge_models(settings: Settings | None = None) -> JudgeModels:
    """Return the global JudgeModels singleton with lazy initialization."""
    global _judge_instance
    if _judge_instance is None:
        with _judge_lock:
            if _judge_instance is None:
                _judge_instance = JudgeModels(settings=settings)
    return _judge_instance


def reset_judge_models() -> None:
    """Reset the global JudgeModels singleton (for test isolation)."""
    global _judge_instance
    with _judge_lock:
        _judge_instance = None


__all__ = [
    "JudgeModels",
    "get_judge_models",
    "reset_judge_models",
]
