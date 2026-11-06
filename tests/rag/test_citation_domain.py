"""Unit tests for Citation domain model relocation, zero-shim invariants, and boundary coercion."""

import pytest
from pydantic import ValidationError

from app.api.schemas import ChatResponse
from app.rag.memory.base import ConversationTurn
from app.rag.state import Citation, CitationList
from app.rag.workflows.base import WorkflowOutput


class TestCitationDomainModel:
    """Tests verifying Citation definition in state.py and zero-shim invariants."""

    def test_citation_defined_in_state_and_immutable(self) -> None:
        citation = Citation(title="Test Doc", url="https://example.com/doc", type="kb")
        assert citation.title == "Test Doc"
        assert citation.url == "https://example.com/doc"
        assert citation.type == "kb"

        # Immutability check
        with pytest.raises(ValidationError):
            citation.title = "Modified"  # type: ignore[misc]

    def test_citation_default_fields(self) -> None:
        citation = Citation(title="Minimal Doc")
        assert citation.title == "Minimal Doc"
        assert citation.url == ""
        assert citation.type == "general"

    def test_citation_reexported_from_tools_package(self) -> None:
        from app.rag.tools import Citation as ToolsCitation
        from app.rag.tools.base import Citation as BaseToolsCitation

        assert ToolsCitation is Citation
        assert BaseToolsCitation is Citation

    def test_citation_zero_shim_no_dict_accessors(self) -> None:
        citation = Citation(title="Doc", url="https://example.com")
        assert citation.title == "Doc"

        with pytest.raises(TypeError):
            _ = citation["title"]  # type: ignore[index]

        assert not hasattr(citation, "get")

    def test_citation_list_annotated_type_exported(self) -> None:
        assert CitationList is not None


class TestBoundaryCoercion:
    """Tests verifying _coerce_citations on WorkflowOutput, ConversationTurn, and ChatResponse."""

    @pytest.mark.parametrize(
        "model_cls, kwargs",
        [
            (WorkflowOutput, {"workflow": "test_wf", "answer": "ok"}),
            (ConversationTurn, {"session_id": "s1", "turn_index": 0, "user_query": "q", "assistant_answer": "a"}),
            (ChatResponse, {"workflow": "test_wf", "answer": "ok", "source_used": "direct"}),
        ],
    )
    def test_coercion_from_citation_instances(self, model_cls, kwargs) -> None:
        c1 = Citation(title="Doc 1", url="http://1", type="kb")
        c2 = Citation(title="Doc 2")
        instance = model_cls(**kwargs, citations=[c1, c2])
        assert instance.citations == [c1, c2]
        assert all(isinstance(c, Citation) for c in instance.citations)

    @pytest.mark.parametrize(
        "model_cls, kwargs",
        [
            (WorkflowOutput, {"workflow": "test_wf", "answer": "ok"}),
            (ConversationTurn, {"session_id": "s1", "turn_index": 0, "user_query": "q", "assistant_answer": "a"}),
            (ChatResponse, {"workflow": "test_wf", "answer": "ok", "source_used": "direct"}),
        ],
    )
    def test_coercion_from_dicts(self, model_cls, kwargs) -> None:
        raw = [
            {"title": "Doc Dict 1", "url": "https://example.com/1", "type": "kb"},
            {"title": "Doc Dict 2"},
        ]
        instance = model_cls(**kwargs, citations=raw)
        assert len(instance.citations) == 2
        assert instance.citations[0] == Citation(title="Doc Dict 1", url="https://example.com/1", type="kb")
        assert instance.citations[1] == Citation(title="Doc Dict 2", url="", type="general")

    @pytest.mark.parametrize(
        "model_cls, kwargs",
        [
            (WorkflowOutput, {"workflow": "test_wf", "answer": "ok"}),
            (ConversationTurn, {"session_id": "s1", "turn_index": 0, "user_query": "q", "assistant_answer": "a"}),
            (ChatResponse, {"workflow": "test_wf", "answer": "ok", "source_used": "direct"}),
        ],
    )
    def test_coercion_from_shorthand_strings(self, model_cls, kwargs) -> None:
        raw = ["report.pdf", "architecture.md"]
        instance = model_cls(**kwargs, citations=raw)
        assert instance.citations == [
            Citation(title="report.pdf"),
            Citation(title="architecture.md"),
        ]

    @pytest.mark.parametrize(
        "model_cls, kwargs",
        [
            (WorkflowOutput, {"workflow": "test_wf", "answer": "ok"}),
            (ConversationTurn, {"session_id": "s1", "turn_index": 0, "user_query": "q", "assistant_answer": "a"}),
            (ChatResponse, {"workflow": "test_wf", "answer": "ok", "source_used": "direct"}),
        ],
    )
    def test_coercion_mixed_list_preserves_order(self, model_cls, kwargs) -> None:
        c0 = Citation(title="Typed Doc", url="https://typed.io", type="web")
        raw = [
            c0,
            {"title": "Dict Doc", "url": "https://dict.io", "type": "kb"},
            "string_doc.txt",
        ]
        instance = model_cls(**kwargs, citations=raw)
        assert len(instance.citations) == 3
        assert instance.citations[0] == c0
        assert instance.citations[1] == Citation(title="Dict Doc", url="https://dict.io", type="kb")
        assert instance.citations[2] == Citation(title="string_doc.txt")

    @pytest.mark.parametrize(
        "model_cls, kwargs",
        [
            (WorkflowOutput, {"workflow": "test_wf", "answer": "ok"}),
            (ConversationTurn, {"session_id": "s1", "turn_index": 0, "user_query": "q", "assistant_answer": "a"}),
            (ChatResponse, {"workflow": "test_wf", "answer": "ok", "source_used": "direct"}),
        ],
    )
    def test_coercion_empty_none_and_whitespace_values(self, model_cls, kwargs) -> None:
        # None
        assert model_cls(**kwargs, citations=None).citations == []
        # Empty list
        assert model_cls(**kwargs, citations=[]).citations == []
        # List of whitespace strings or empty dicts / falsy values
        assert model_cls(**kwargs, citations=["", "   "]).citations == []
        # Single string / dict instead of list
        single_str = model_cls(**kwargs, citations="single_doc.txt")
        assert single_str.citations == [Citation(title="single_doc.txt")]
        single_dict = model_cls(**kwargs, citations={"title": "single_dict"})
        assert single_dict.citations == [Citation(title="single_dict")]

    @pytest.mark.parametrize(
        "model_cls, kwargs",
        [
            (WorkflowOutput, {"workflow": "test_wf", "answer": "ok"}),
            (ConversationTurn, {"session_id": "s1", "turn_index": 0, "user_query": "q", "assistant_answer": "a"}),
            (ChatResponse, {"workflow": "test_wf", "answer": "ok", "source_used": "direct"}),
        ],
    )
    def test_coercion_from_source_dict_fallback(self, model_cls, kwargs) -> None:
        raw = [
            {"source": "it_guide.md"},
            {"source": "network.pdf", "url": "https://network.pdf", "type": "kb"},
        ]
        instance = model_cls(**kwargs, citations=raw)
        assert len(instance.citations) == 2
        assert instance.citations[0] == Citation(title="it_guide.md")
        assert instance.citations[1] == Citation(title="network.pdf", url="https://network.pdf", type="kb")
