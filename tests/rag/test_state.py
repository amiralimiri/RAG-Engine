"""Unit tests for Attachment domain model, coerce_attachments boundary validator, and AgentState."""

import operator
from typing import Annotated, get_args, get_origin

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field, ValidationError

from app.rag.state import (
    AgentState,
    Attachment,
    AttachmentList,
    Citation,
    coerce_attachments,
)


class DummyModelWithAttachments(BaseModel):
    """Test model leveraging AttachmentList BeforeValidator."""

    attachments: AttachmentList = Field(default_factory=list)


class TestAttachmentDomainModel:
    """Tests verifying Attachment domain model definition, immutability, and repr safety."""

    def test_attachment_creation_and_fields(self) -> None:
        att = Attachment(
            id="att-1",
            mime_type="application/pdf",
            url="https://example.com/doc.pdf",
            content_bytes=b"sample binary bytes",
            metadata={"source": "manual_upload", "author": "alice"},
        )
        assert att.id == "att-1"
        assert att.mime_type == "application/pdf"
        assert att.url == "https://example.com/doc.pdf"
        assert att.content_bytes == b"sample binary bytes"
        assert att.metadata == {"source": "manual_upload", "author": "alice"}

    def test_attachment_defaults(self) -> None:
        att = Attachment(id="att-2", mime_type="text/plain")
        assert att.id == "att-2"
        assert att.mime_type == "text/plain"
        assert att.url is None
        assert att.content_bytes is None
        assert att.metadata == {}

    def test_attachment_immutability(self) -> None:
        att = Attachment(id="att-3", mime_type="image/png")
        with pytest.raises(ValidationError):
            att.id = "new-id"  # type: ignore[misc]

        with pytest.raises(ValidationError):
            att.mime_type = "image/jpeg"  # type: ignore[misc]

    def test_attachment_metadata_is_immutable_in_place(self) -> None:
        att = Attachment(
            id="att-4",
            mime_type="text/plain",
            metadata={"source": "manual_upload"},
        )

        with pytest.raises(TypeError):
            att.metadata["source"] = "mutated"  # type: ignore[index]

        with pytest.raises(TypeError):
            att.metadata.update({"source": "mutated"})  # type: ignore[attr-defined]

        with pytest.raises(TypeError):
            del att.metadata["source"]  # type: ignore[attr-defined]

        assert att.metadata == {"source": "manual_upload"}

    def test_attachment_default_metadata_is_immutable_in_place(self) -> None:
        att = Attachment(id="att-5", mime_type="application/pdf")

        with pytest.raises(TypeError):
            att.metadata["source"] = "mutated"  # type: ignore[index]

        assert att.metadata == {}

    def test_attachment_metadata_nested_values_are_immutable(self) -> None:
        att = Attachment(
            id="att-6",
            mime_type="application/json",
            metadata={"tags": ["alpha", "beta"], "nested": {"owner": "alice"}},
        )

        with pytest.raises(TypeError):
            att.metadata["nested"]["owner"] = "mallory"  # type: ignore[index]

        with pytest.raises(AttributeError):
            att.metadata["tags"].append("gamma")  # type: ignore[attr-defined]

        assert att.metadata["tags"] == ("alpha", "beta")
        assert att.metadata["nested"] == {"owner": "alice"}

    def test_attachment_repr_suppresses_content_bytes(self) -> None:
        att = Attachment(
            id="att-secret",
            mime_type="application/octet-stream",
            content_bytes=b"super_secret_payload_and_binary_data",
        )
        rendered_repr = repr(att)
        assert "att-secret" in rendered_repr
        assert "application/octet-stream" in rendered_repr
        assert "super_secret_payload" not in rendered_repr
        assert "content_bytes" not in rendered_repr


class TestCoerceAttachments:
    """Tests verifying coerce_attachments boundary validator."""

    def test_coercion_from_attachment_instances(self) -> None:
        a1 = Attachment(id="1", mime_type="image/png", url="https://example.com/1.png")
        a2 = Attachment(id="2", mime_type="text/plain")
        res = coerce_attachments([a1, a2])
        assert res == [a1, a2]
        assert all(isinstance(x, Attachment) for x in res)

    def test_coercion_from_canonical_dicts(self) -> None:
        raw = [
            {"id": "doc1", "mime_type": "application/pdf", "url": "https://example.com/doc1.pdf"},
            {"id": "doc2", "mime_type": "image/jpeg", "metadata": {"width": 800}},
        ]
        res = coerce_attachments(raw)
        assert len(res) == 2
        assert res[0] == Attachment(id="doc1", mime_type="application/pdf", url="https://example.com/doc1.pdf")
        assert res[1] == Attachment(id="doc2", mime_type="image/jpeg", metadata={"width": 800})

    def test_coercion_from_legacy_dict_keys(self) -> None:
        # Legacy formats using filename / content_type or name / type
        raw = [
            {"filename": "specs.pdf", "content_type": "application/pdf"},
            {"name": "photo.png", "type": "image/png"},
        ]
        res = coerce_attachments(raw)
        assert len(res) == 2
        assert res[0].id == "specs.pdf"
        assert res[0].mime_type == "application/pdf"
        assert res[1].id == "photo.png"
        assert res[1].mime_type == "image/png"

    def test_coercion_from_shorthand_strings(self) -> None:
        raw = ["architecture.pdf", "diagram.png", "unknown_format"]
        res = coerce_attachments(raw)
        assert len(res) == 3
        assert res[0].id == "architecture.pdf"
        assert res[0].mime_type == "application/pdf"
        assert res[1].id == "diagram.png"
        assert res[1].mime_type == "image/png"
        assert res[2].id == "unknown_format"
        assert res[2].mime_type == "application/octet-stream"

    def test_coercion_from_url_string(self) -> None:
        raw = ["https://example.com/images/screenshot.png"]
        res = coerce_attachments(raw)
        assert len(res) == 1
        assert res[0].id == "https://example.com/images/screenshot.png"
        assert res[0].url == "https://example.com/images/screenshot.png"
        assert res[0].mime_type == "image/png"

    def test_coercion_from_base_model_instances(self) -> None:
        class ForeignAttachment(BaseModel):
            filename: str
            content_type: str
            url: str | None = None

        foreign = ForeignAttachment(
            filename="legacy.pdf",
            content_type="application/pdf",
            url="https://example.com/legacy.pdf",
        )
        res = coerce_attachments([foreign])
        assert len(res) == 1
        assert res[0].id == "legacy.pdf"
        assert res[0].mime_type == "application/pdf"
        assert res[0].url == "https://example.com/legacy.pdf"

    def test_coercion_empty_none_and_whitespace_values(self) -> None:
        assert coerce_attachments(None) == []
        assert coerce_attachments([]) == []
        assert coerce_attachments(["", "   "]) == []
        # Single string / dict instead of list
        assert coerce_attachments("single.pdf") == [Attachment(id="single.pdf", mime_type="application/pdf")]
        assert coerce_attachments({"id": "d1", "mime_type": "text/plain"}) == [
            Attachment(id="d1", mime_type="text/plain")
        ]

    def test_coercion_mixed_list_preserves_order(self) -> None:
        a1 = Attachment(id="existing", mime_type="text/csv")
        raw = [
            a1,
            {"id": "doc.pdf", "mime_type": "application/pdf"},
            "log.txt",
        ]
        res = coerce_attachments(raw)
        assert len(res) == 3
        assert res[0] == a1
        assert res[1].id == "doc.pdf"
        assert res[1].mime_type == "application/pdf"
        assert res[2].id == "log.txt"
        assert res[2].mime_type == "text/plain"

    def test_coercion_invalid_dict_raises_validation_error(self) -> None:
        raw_invalid = [{"unknown_attribute": "value"}]
        with pytest.raises(ValidationError):
            coerce_attachments(raw_invalid)

    def test_attachment_list_validator_integration(self) -> None:
        model = DummyModelWithAttachments.model_validate(
            {
                "attachments": [
                    "guide.pdf",
                    {"id": "data.json", "mime_type": "application/json"},
                ]
            }
        )
        assert len(model.attachments) == 2
        assert model.attachments[0] == Attachment(id="guide.pdf", mime_type="application/pdf")
        assert model.attachments[1] == Attachment(id="data.json", mime_type="application/json")


class TestAgentStateSchema:
    """Tests verifying AgentState blackboard evolution and legacy key preservation."""

    def test_agent_state_total_false_allows_partial_initialization(self) -> None:
        partial: AgentState = {
            "question": "How to deploy?",
            "messages": [HumanMessage(content="How to deploy?")],
        }
        assert partial["question"] == "How to deploy?"
        assert len(partial["messages"]) == 1

    def test_agent_state_all_keys_annotated(self) -> None:
        annotations = AgentState.__annotations__

        # Blackboard keys
        assert "question" in annotations
        assert "current_query" in annotations
        assert "messages" in annotations
        assert "attachments" in annotations
        assert "context" in annotations
        assert "active_agent" in annotations
        assert "citations" in annotations
        assert "trace" in annotations
        assert "trace_id" in annotations
        assert "retrieved_contexts" in annotations
        assert "answer" in annotations
        assert "source_used" in annotations

        # Backward compatibility keys
        assert "kb_docs" in annotations
        assert "web_results" in annotations
        assert "kb_grade" in annotations
        assert "web_grade" in annotations
        assert "retry_count" in annotations

    def test_agent_state_reducers_configured(self) -> None:
        annotations = AgentState.__annotations__

        # messages reducer
        messages_anno = annotations["messages"]
        assert get_origin(messages_anno) is Annotated
        args = get_args(messages_anno)
        assert operator.add in args

        # trace reducer
        trace_anno = annotations["trace"]
        assert get_origin(trace_anno) is Annotated
        args = get_args(trace_anno)
        assert operator.add in args

    def test_agent_state_supports_full_blackboard_and_legacy_usage(self) -> None:
        full_state: AgentState = {
            "question": "What is the policy?",
            "current_query": "company leave policy",
            "messages": [
                HumanMessage(content="What is the policy?"),
                AIMessage(content="Here is the policy."),
            ],
            "attachments": [Attachment(id="policy.pdf", mime_type="application/pdf")],
            "context": {"department": "HR", "sub_score": 0.95},
            "active_agent": "hr_specialist",
            "citations": [Citation(title="Leave Policy", url="https://example.com/leave")],
            "trace": ["route_start", "retrieve_kb", "generate_from_kb"],
            "trace_id": "tr-12345",
            "retrieved_contexts": ["Annual leave is 20 days."],
            "answer": "Annual leave is 20 days per year.",
            "source_used": "kb",
            "kb_docs": [Document(page_content="Annual leave is 20 days.")],
            "web_results": "",
            "kb_grade": "good",
            "web_grade": "good",
            "retry_count": 0,
        }
        assert full_state["active_agent"] == "hr_specialist"
        assert full_state["context"]["department"] == "HR"
        assert len(full_state["attachments"]) == 1
        assert full_state["attachments"][0].id == "policy.pdf"
