import logging
import re
from dataclasses import dataclass
from typing import Any

from langfuse.model import TextPromptClient

from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client

logger = logging.getLogger(__name__)


@dataclass
class FallbackPrompt:
    """Lightweight prompt interface compatible with Langfuse TextPromptClient."""

    name: str
    template: str
    version: int = 0
    is_fallback: bool = True

    def compile(self, **kwargs: Any) -> str:
        """Replace mustache {{variable}} placeholders with kwargs."""
        text = self.template
        for key, value in kwargs.items():
            pattern = re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}")
            text = pattern.sub(lambda _: str(value), text)
        return text

    @property
    def prompt(self) -> str:
        return self.template


LOCAL_PROMPTS: dict[str, str] = {
    "rag:router:llm": """You route messages for an enterprise IT support assistant.
Use kb for questions about company IT policies, VPN, password reset, MFA, laptop setup,
software access, security, email, devices, troubleshooting, or technology support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
Question: {{question}}
Return valid JSON like {"route":"kb"}.""",

    "rag:router:jev": """Which path should this enterprise IT support question take?
Question: {{question}}
Options:
- kb: Questions about company IT policies, VPN, password reset, MFA, laptop setup, software access, security, email, devices, troubleshooting, or technology support.
- direct: Only greetings, thanks, or casual chat that needs no company knowledge.""",

    "rag:grade_kb:llm": """You grade evidence for an enterprise IT support assistant.

Original user question:
{{question}}

Query used for retrieval:
{{current_query}}

Private company KB evidence:
{{context}}

Evaluate whether the evidence is sufficient to answer the ORIGINAL user question confidently and specifically.
The retrieval query is only a search aid. Do not grade the evidence as good merely because it is relevant to the retrieval query.
Return good only if the evidence supports a sufficiently specific answer to the original question. Otherwise return weak.

Return valid JSON:
{"grade": "good"} or {"grade": "weak"}.""",

    "rag:grade_kb:jev": """Question: {{question}}

Private company KB evidence:
{{context}}

Is the provided private company KB evidence sufficient to answer the question confidently and specifically?""",

    "rag:grade_web:llm": """You grade web evidence for an enterprise IT support assistant.

Original user question:
{{question}}

Query used for web search:
{{current_query}}

Web evidence:
{{web_results}}

Evaluate whether the web evidence is sufficient, relevant, and specific enough to answer the ORIGINAL user question confidently.
The search query is only a retrieval aid. Do not grade the evidence as good merely because it matches the search query.
Use good only when the evidence directly supports a sufficiently specific answer to the original question. Otherwise, return weak.

Return valid JSON:
{"grade": "good"} or {"grade": "weak"}.""",

    "rag:grade_web:jev": """Question: {{question}}

Web evidence:
{{web_results}}

Is the external web documentation sufficient to answer the user question confidently?""",

    "rag:rewrite_query": """You improve search queries for an enterprise IT assistant.

Original user question:
{{question}}

Previous query:
{{current_query}}

Create a meaningfully improved query that preserves the original intent and addresses the missing information.
Do not answer the question. Return only the new query.""",

    "rag:generate_kb": """You are an enterprise IT support copilot. Answer ONLY from the private company KB below.
Be concise, actionable, and safe. If steps are present, present them clearly.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
Question: {{question}}

Private KB:
{{context}}""",

    "rag:generate_web": """You are an enterprise IT support copilot. The private company KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external web information and may need IT validation before changing company-managed systems.
Question: {{question}}

Web evidence:
{{web_results}}""",

    "rag:direct_answer": """Respond briefly, warmly, and naturally as an enterprise IT support copilot to: {{question}}""",
}


_seeded_prompts: set[str] = set()


def seed_prompt(name: str) -> bool:
    """Safely create/seed a canonical prompt into Langfuse Prompt CMS once per process."""
    if name in _seeded_prompts:
        return True
    settings = get_settings()
    if not settings.langfuse_enabled:
        return False
    client = get_langfuse_client()
    if not client or not hasattr(client, "create_prompt"):
        return False
    fallback_text = LOCAL_PROMPTS.get(name)
    if not fallback_text:
        return False

    _seeded_prompts.add(name)
    try:
        client.create_prompt(
            name=name,
            prompt=fallback_text,
            labels=["production"],
            type="text",
        )
        logger.info("Auto-seeded prompt '%s' into Langfuse Prompt CMS with label 'production'", name)
        return True
    except Exception as exc:
        logger.warning("Could not auto-seed prompt '%s' into Langfuse: %s", name, exc)
        return False


def seed_all_prompts() -> dict[str, bool]:
    """Seed all 10 canonical prompts into Langfuse Prompt CMS if enabled."""
    results: dict[str, bool] = {}
    settings = get_settings()
    if not settings.langfuse_enabled:
        return results
    client = get_langfuse_client()
    if not client or not hasattr(client, "create_prompt"):
        return results

    for name, fallback_text in LOCAL_PROMPTS.items():
        if name in _seeded_prompts:
            results[name] = True
            continue
        try:
            client.create_prompt(
                name=name,
                prompt=fallback_text,
                labels=["production"],
                type="text",
            )
            _seeded_prompts.add(name)
            results[name] = True
            logger.info("Seeded prompt '%s' into Langfuse Prompt CMS", name)
        except Exception as exc:
            _seeded_prompts.add(name)
            results[name] = False
            logger.warning("Failed to seed prompt '%s' into Langfuse: %s", name, exc)
    return results


def get_workflow_prompt(name: str) -> TextPromptClient | FallbackPrompt:
    """Retrieve prompt from Langfuse Prompt CMS with local caching and fail-open fallback."""
    settings = get_settings()
    fallback_text = LOCAL_PROMPTS.get(name, "")

    client = get_langfuse_client()
    if client and settings.langfuse_enabled:
        try:
            prompt = client.get_prompt(
                name=name,
                type="text",
                cache_ttl_seconds=settings.langfuse_prompt_cache_ttl_seconds,
                fallback=fallback_text,
            )
            # If the returned prompt is a fallback or has fallback flag, auto-seed to Langfuse
            if getattr(prompt, "is_fallback", False) is True:
                seed_prompt(name)
            return prompt
        except Exception as exc:
            logger.warning(
                "Failed to fetch prompt '%s' from Langfuse Prompt CMS: %s. Using local fallback.",
                name,
                exc,
            )
            seed_prompt(name)

    return FallbackPrompt(name=name, template=fallback_text, version=0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Seeding canonical prompts into Langfuse Prompt CMS...")
    seeded_status = seed_all_prompts()
    for prompt_key, is_ok in seeded_status.items():
        print(f"  {prompt_key}: {'seeded' if is_ok else 'failed/skipped'}")
