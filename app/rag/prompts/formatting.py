"""Shared conversational formatting helpers for prompt compilation."""

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def format_chat_history(messages: Any) -> str:
    """Format conversational messages into a compact role-prefixed transcript."""
    if not messages or not isinstance(messages, Sequence):
        return ""
    lines: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            role = "Human"
            content = m.content
        elif isinstance(m, AIMessage):
            role = "Assistant"
            content = m.content
        elif isinstance(m, SystemMessage):
            role = "System"
            content = m.content
        elif isinstance(m, dict):
            role = (m.get("role") or "User").capitalize()
            content = m.get("content", "")
        else:
            role = getattr(m, "type", getattr(m, "role", "Message")).capitalize()
            content = getattr(m, "content", str(m))
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


__all__ = ["format_chat_history"]
