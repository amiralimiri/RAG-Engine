import operator
from collections.abc import Sequence
from typing import Annotated, Any, Literal, NotRequired

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from typing_extensions import TypedDict


class Citation(BaseModel):
    """Strongly-typed reference citation attributed to retrieved evidence."""

    model_config = ConfigDict(frozen=True)

    title: str
    url: str = ""
    type: str = "general"


def coerce_citations(val: Sequence[Any] | Any | None = None) -> list[Citation]:
    """Boundary validator coercing legacy dictionaries and shorthand strings into Citation models."""
    if not val:
        return []

    def _to_citation(item: Any) -> Citation | None:
        if isinstance(item, Citation):
            return item
        if isinstance(item, BaseModel):
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return _to_citation(dumped)
            return None
        if isinstance(item, dict):
            # If title is missing, fallback to source attribute if present
            data = dict(item)
            if not data.get("title") and data.get("source"):
                source_val = str(data.get("source")).strip()
                if source_val:
                    data["title"] = source_val
            # Construct Citation directly to enforce validation (raises ValidationError if invalid)
            return Citation(**data)
        if isinstance(item, str) and item.strip():
            return Citation(title=item.strip())
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


CitationList = Annotated[list[Citation], BeforeValidator(coerce_citations)]


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
    citations: Sequence[Citation]
    trace_id: NotRequired[str]
    retrieved_contexts: NotRequired[list[str]]
    messages: Annotated[list[BaseMessage], operator.add]
    attachments: NotRequired[list[dict[str, Any]]]
