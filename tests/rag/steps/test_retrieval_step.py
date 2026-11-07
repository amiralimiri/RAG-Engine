"""Unit tests for ToolRetrievalStep in complete isolation."""

from typing import Any

import pytest
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig

from app.rag.state import Citation
from app.rag.steps import ToolRetrievalStep, get_step_class
from app.rag.tools import FakeRAGTool, ToolEvidenceArtifact


def _kb_tool() -> FakeRAGTool:
    return FakeRAGTool(
        name="knowledge_base_search",
        content="KB display content",
        artifact=ToolEvidenceArtifact(
            citations=[Citation(title="vpn.md", type="private_kb")],
            retrieved_contexts=["Use OpenVPN with the company profile."],
            source_used="vectorstore",
            raw_payload=[
                Document(page_content="Use OpenVPN with the company profile.", metadata={"source": "docs/vpn.md"}),
                Document(page_content="Contact IT for credentials.", metadata={"source": "docs/help.md"}),
            ],
        ),
    )


@pytest.mark.asyncio
async def test_execute_emits_default_evidence_outputs() -> None:
    tool = _kb_tool()
    step = ToolRetrievalStep(tool)
    config: RunnableConfig = {"tags": ["retrieval"]}
    state: dict[str, Any] = {"question": "How do I connect to VPN?", "current_query": "vpn setup"}

    result = await step(state, config)

    assert result["retrieved_contexts"] == ["Use OpenVPN with the company profile."]
    assert result["citations"] == [Citation(title="vpn.md", type="private_kb")]
    assert result["source_used"] == "vectorstore"
    assert result["trace"] == ["Tool retrieval → 2 results"]
    assert "documents" not in result
    assert "content" not in result
    assert tool.aexecute_calls == [{"input": {"query": "vpn setup"}, "config": config}]


@pytest.mark.asyncio
async def test_execute_prefers_current_query_over_stale_top_level_query() -> None:
    tool = _kb_tool()
    step = ToolRetrievalStep(tool)
    state: dict[str, Any] = {
        "question": "How do I connect to VPN?",
        "current_query": "vpn setup",
        "query": "stale legacy query",
    }

    await step(state)

    assert tool.aexecute_calls[0]["input"] == {"query": "vpn setup"}


@pytest.mark.asyncio
async def test_execute_can_preserve_existing_source_used_decision() -> None:
    tool = _kb_tool()
    step = ToolRetrievalStep(tool, emit_source_used=False)

    result = await step({"question": "vpn", "source_used": "kb"})

    assert "source_used" not in result


@pytest.mark.asyncio
async def test_execute_falls_back_to_question_when_current_query_missing() -> None:
    tool = _kb_tool()
    step = ToolRetrievalStep(tool)

    await step({"question": "How do I reset my password?"})

    assert tool.aexecute_calls[0]["input"] == {"query": "How do I reset my password?"}


@pytest.mark.asyncio
async def test_execute_uses_tool_content_when_artifact_contexts_are_empty() -> None:
    tool = FakeRAGTool(
        content="Only display content",
        artifact=ToolEvidenceArtifact(source_used="web_search"),
    )
    step = ToolRetrievalStep(tool)

    result = await step({"question": "Search the web"})

    assert result["retrieved_contexts"] == ["Only display content"]
    assert result["source_used"] == "web_search"


@pytest.mark.asyncio
async def test_execute_honors_nested_input_and_output_mappings() -> None:
    tool = _kb_tool()
    step = ToolRetrievalStep(
        tool,
        inputs={"query": "context.search.query"},
        outputs={
            "retrieved_contexts": "context.kb.chunks",
            "citations": "context.kb.citations",
            "source_used": "context.kb.source",
            "trace": "context.kb.trace",
            "documents": "kb_docs",
            "content": "web_results",
        },
    )
    state: dict[str, Any] = {
        "context": {
            "search": {"query": "nested vpn query"},
            "kb": {"prior": "preserved"},
        }
    }

    result = await step(state)

    assert tool.aexecute_calls[0]["input"] == {"query": "nested vpn query"}
    assert result["context"]["kb"]["chunks"] == ["Use OpenVPN with the company profile."]
    assert result["context"]["kb"]["citations"] == [Citation(title="vpn.md", type="private_kb")]
    assert result["context"]["kb"]["source"] == "vectorstore"
    assert result["context"]["kb"]["prior"] == "preserved"
    assert len(result["kb_docs"]) == 2
    assert result["web_results"] == "KB display content"


@pytest.mark.asyncio
async def test_execute_renders_custom_trace_template_with_document_count() -> None:
    tool = _kb_tool()
    step = ToolRetrievalStep(tool, trace_template="Private KB retrieval → {count} chunks")

    result = await step({"question": "vpn"})

    assert result["trace"] == ["Private KB retrieval → 2 chunks"]


@pytest.mark.asyncio
async def test_step_is_callable_as_langgraph_node() -> None:
    tool = _kb_tool()
    step = ToolRetrievalStep(tool)

    result = await step({"question": "vpn"})

    assert result["source_used"] == "vectorstore"


def test_step_is_registered_under_canonical_alias() -> None:
    assert get_step_class("tool_retrieval") is ToolRetrievalStep
