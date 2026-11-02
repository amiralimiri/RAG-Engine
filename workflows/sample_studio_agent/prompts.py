"""Prompt variants for Sample Studio Research Agent."""

from __future__ import annotations

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful domain research assistant exported from an external studio. "
    "Provide clear, evidence-based answers based on the retrieved contexts."
)

CONCISE_SYSTEM_PROMPT = "You are an executive summary assistant. Answer concisely in 2 sentences maximum."

PROMPT_VARIANTS: dict[str, str] = {
    "default": DEFAULT_SYSTEM_PROMPT,
    "concise": CONCISE_SYSTEM_PROMPT,
}


def get_prompt_template(variant: str = "default") -> str:
    """Resolve prompt template variant by identifier."""
    return PROMPT_VARIANTS.get(variant, DEFAULT_SYSTEM_PROMPT)
