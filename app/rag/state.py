import mimetypes
import operator
from collections.abc import Sequence
from typing import Annotated, Any, Literal

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


class Attachment(BaseModel):
    """Immutable domain model representing multimodal and file payloads."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique identifier for the attachment")
    mime_type: str = Field(description="IANA media type (e.g. image/png, application/pdf)")
    url: str | None = Field(default=None, description="External or object storage URL")
    content_bytes: bytes | None = Field(default=None, repr=False, description="Raw content bytes if staged")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary file metadata")


def coerce_attachments(val: Sequence[Any] | Any | None = None) -> list[Attachment]:
    """Boundary validator coercing legacy dictionaries, shorthand strings, and models into Attachment models."""
    if not val:
        return []

    def _to_attachment(item: Any) -> Attachment | None:
        if isinstance(item, Attachment):
            return item
        if isinstance(item, BaseModel):
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return _to_attachment(dumped)
            return None
        if isinstance(item, dict):
            data = dict(item)
            if not data.get("id"):
                id_val = data.get("filename") or data.get("name") or data.get("url")
                if id_val:
                    data["id"] = str(id_val).strip()
            if not data.get("mime_type"):
                mime_val = data.get("content_type") or data.get("type")
                if mime_val:
                    data["mime_type"] = str(mime_val).strip()
                elif data.get("id"):
                    guessed, _ = mimetypes.guess_type(str(data["id"]))
                    if guessed:
                        data["mime_type"] = guessed
                elif data.get("url"):
                    guessed, _ = mimetypes.guess_type(str(data["url"]))
                    if guessed:
                        data["mime_type"] = guessed
            return Attachment(**data)
        if isinstance(item, str) and item.strip():
            s = item.strip()
            guessed_mime, _ = mimetypes.guess_type(s)
            mime = guessed_mime or "application/octet-stream"
            url = s if (s.startswith("http://") or s.startswith("https://")) else None
            return Attachment(id=s, mime_type=mime, url=url)
        return None

    if isinstance(val, (list, tuple, set)):
        coerced: list[Attachment] = []
        for item in val:
            a = _to_attachment(item)
            if a is not None:
                coerced.append(a)
        return coerced

    a = _to_attachment(val)
    return [a] if a is not None else []


AttachmentList = Annotated[list[Attachment], BeforeValidator(coerce_attachments)]


class RouteDecision(BaseModel):
    route: Literal["kb", "direct"] = Field(description="kb for IT/support questions; direct for greetings/simple chat")


class EvidenceGrade(BaseModel):
    grade: Literal["good", "weak"] = Field(description="Whether evidence is sufficient to answer")


class AgentState(TypedDict, total=False):
    # Primary message-centric blackboard
    question: str
    current_query: str
    messages: Annotated[list[BaseMessage], operator.add]
    attachments: list[Attachment]
    context: dict[str, Any]
    active_agent: str | None
    citations: Sequence[Citation]
    trace: Annotated[list[str], operator.add]
    trace_id: str
    retrieved_contexts: list[str]
    answer: str
    source_used: str

    # Backward-compatibility fields (preserved for legacy self-corrective tests/nodes)
    kb_docs: list[Document]
    web_results: str
    kb_grade: str
    web_grade: str
    retry_count: int
