import operator
from collections.abc import Sequence
from typing import Annotated, Any, Literal, NotRequired

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict


class Citation(BaseModel):
    """Strongly-typed reference citation attributed to retrieved evidence."""

    model_config = ConfigDict(frozen=True)

    title: str
    url: str = ""
    type: str = "general"


def coerce_citations(val: Any) -> list[Citation]:
    """Boundary validator coercing legacy dictionaries and shorthand strings into Citation models."""
    if not val:
        return []

    def _to_citation(item: Any) -> Citation | None:
        if isinstance(item, Citation):
            return item
        if isinstance(item, dict):
            # If title is directly present, prefer it
            title = item.get("title")
            if not title:
                # Fallback to legacy dictionary keys: source, id, text, snippet, filename
                for key in ("source", "id", "text", "snippet", "filename"):
                    candidate = item.get(key)
                    if candidate and str(candidate).strip():
                        title = str(candidate).strip()
                        break
            if not title:
                return None
            url = str(item.get("url") or "")
            ctype = str(item.get("type") or "general")
            return Citation(title=str(title).strip(), url=url, type=ctype)
        if isinstance(item, str) and item.strip():
            return Citation(title=item.strip())
        if not isinstance(item, str) and hasattr(item, "model_dump"):
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return _to_citation(dumped)
        return None

    if isinstance(val, (list, tuple, set)):
        coerced: list[Citation] = []
        for item in val:
            c = _to_citation(item)
            if c is not None:
                coerced.append(c)
        return coerced

    c = _to_citation(val)
    return [c] if c is not None else []


class RouteDecision(BaseModel):
    route: Literal["kb", "direct"] = Field(description="kb for IT/support questions; direct for greetings/simple chat")


class EvidenceGrade(BaseModel):
    grade: Literal["good", "weak"] = Field(description="Whether evidence is sufficient to answer")


class AgentState(TypedDict):
    question: str
    current_query: str
    kb_docs: list[Document]
    web_results: str
    kb_grade: str
    web_grade: str
    answer: str
    source_used: str
    retry_count: int
    trace: Annotated[list[str], operator.add]
    citations: list[dict]
    trace_id: NotRequired[str]
    retrieved_contexts: NotRequired[list[str]]
    messages: Annotated[list[BaseMessage], operator.add]
    attachments: NotRequired[list[dict[str, Any]]]
