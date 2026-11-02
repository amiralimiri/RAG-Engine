"""Domain models, template interpolation, and protocol seam for prompt management."""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

CANONICAL_PROMPTS: dict[str, str] = {
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
    "rag:condense_query": """Given the following conversation and a follow up question, rephrase the follow up question to be a standalone search query.
Chat History:
{{chat_history}}

Follow Up: {{question}}
Standalone question:""",
}


def interpolate_mustache(template: str, **variables: Any) -> str:
    """Compile template by replacing {{ variable }} placeholders with values in a single pass.

    Trims whitespace inside brackets, safely handles backslashes in variable values
    via callable replacer, and preserves unprovided placeholders fail-open.
    """
    if not variables:
        return template

    pattern = re.compile(
        r"\{\{\s*(" + "|".join(re.escape(k) for k in sorted(variables.keys(), key=len, reverse=True)) + r")\s*\}\}"
    )
    str_vars = {k: str(v) for k, v in variables.items()}
    return pattern.sub(lambda m: str_vars[m.group(1)], template)


class RenderedPrompt(BaseModel):
    """Domain value object encapsulating compiled prompt text and execution config."""

    name: str
    text: str
    config: dict[str, Any] = Field(default_factory=dict)
    raw_prompt: Any = None

    def __str__(self) -> str:
        """Coerce seamlessly to compiled text when passed into LLMs or format strings."""
        return self.text


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
