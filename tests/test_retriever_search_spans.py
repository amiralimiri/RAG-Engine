from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.rag import engine
from app.rag.classifiers import OpenAIClassifierAdapter
from app.rag.engine import create_agentic_rag_graph
from app.rag.tools import create_retriever_tool, create_web_search_tool
from app.services.prompts import FallbackPrompt, get_workflow_prompt


class RecordingCallbackHandler(BaseCallbackHandler):
    """Test callback handler tracking retriever and tool lifecycle events."""

    def __init__(self):
        super().__init__()
        self.retriever_starts = []
        self.retriever_ends = []
        self.tool_starts = []
        self.tool_ends = []

    def on_retriever_start(self, serialized, query, **kwargs):
        self.retriever_starts.append(query)

    def on_retriever_end(self, documents, **kwargs):
        self.retriever_ends.append(documents)

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tool_starts.append(input_str)

    def on_tool_end(self, output, **kwargs):
        self.tool_ends.append(output)


class DummyVectorRetriever(BaseRetriever):
    """Dummy retriever to test standard LangChain callback firing."""

    def _get_relevant_documents(self, query: str, *, run_manager=None):
        return [Document(page_content=f"Content for {query}", metadata={"source": "kb/doc.md"})]


@tool
def dummy_web_search(query: str) -> dict:
    """Mock web search tool firing LangChain tool callbacks."""
    return {
        "answer": f"Web answer for {query}",
        "results": [{"title": f"Result for {query}", "url": "https://example.com", "content": "mock"}],
    }


# ==============================================================================
# 1. create_retriever_tool config propagation & callbacks
# ==============================================================================

def test_fix_single_llm_retrieve_kb_signature_and_config_forwarding():
    """create_retriever_tool accepts config: RunnableConfig and passes it to retriever.invoke."""
    mock_retriever = MagicMock()
    mock_docs = [Document(page_content="VPN info", metadata={"source": "kb/vpn.md"})]
    mock_retriever.invoke.return_value = mock_docs

    kb_tool = create_retriever_tool(retriever=mock_retriever)
    custom_config: RunnableConfig = {"tags": ["unit-test-llm"], "metadata": {"trace_id": "trace-123"}}

    content, artifact = kb_tool.invoke({"query": "how do I connect to VPN?"}, config=custom_config)

    mock_retriever.invoke.assert_called_once_with("how do I connect to VPN?", config=custom_config)
    assert artifact["documents"] == mock_docs
    assert len(artifact["citations"]) == 1
    assert "VPN info" in content


def test_fix_single_llm_retrieve_kb_default_config_none():
    """create_retriever_tool defaults config to None when called without config."""
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = []

    kb_tool = create_retriever_tool(retriever=mock_retriever)
    content, artifact = kb_tool.invoke({"query": "mfa setup"})

    mock_retriever.invoke.assert_called_once_with("mfa setup", config=None)
    assert artifact["documents"] == []


def test_fix_single_llm_retrieve_kb_triggers_retriever_callbacks():
    """create_retriever_tool fires on_retriever_start, on_retriever_end, on_tool_start, and on_tool_end via RunnableConfig."""
    handler = RecordingCallbackHandler()
    config: RunnableConfig = {"callbacks": [handler]}

    kb_tool = create_retriever_tool(retriever=DummyVectorRetriever())
    content, artifact = kb_tool.invoke({"query": "password policy"}, config=config)

    assert len(handler.retriever_starts) == 1
    assert handler.retriever_starts[0] == "password policy"
    assert len(handler.retriever_ends) == 1
    assert handler.retriever_ends[0][0].page_content == "Content for password policy"
    assert len(handler.tool_starts) >= 1
    assert len(handler.tool_ends) >= 1


# ==============================================================================
# 2. create_web_search_tool config propagation & callbacks
# ==============================================================================

def test_fix_single_llm_search_web_signature_and_config_forwarding():
    """create_web_search_tool accepts config: RunnableConfig and passes it to search_client.invoke."""
    mock_search = MagicMock()
    mock_search.invoke.return_value = {
        "answer": "GlobalProtect setup guide",
        "results": [{"title": "VPN Guide", "url": "https://example.com/vpn", "content": "Install steps"}],
    }

    custom_config: RunnableConfig = {"tags": ["tavily-test"], "metadata": {"source": "web"}}
    web_tool = create_web_search_tool(search_client=mock_search)

    content, artifact = web_tool.invoke({"query": "pulse secure setup"}, config=custom_config)

    mock_search.invoke.assert_called_once_with({"query": "pulse secure setup"}, config=custom_config)
    assert len(artifact["citations"]) == 1
    assert "Search answer: GlobalProtect setup guide" in content
    assert len(artifact["retrieved_contexts"]) == 1


def test_fix_single_llm_search_web_default_config_none():
    """create_web_search_tool defaults config to None when called without config."""
    mock_search = MagicMock()
    mock_search.invoke.return_value = {"results": []}

    web_tool = create_web_search_tool(search_client=mock_search)
    content, artifact = web_tool.invoke({"query": "zoom client install"})

    mock_search.invoke.assert_called_once_with({"query": "zoom client install"}, config=None)
    assert artifact["citations"] == []


def test_fix_single_llm_search_web_triggers_tool_callbacks():
    """create_web_search_tool fires on_tool_start and on_tool_end via RunnableConfig."""
    handler = RecordingCallbackHandler()
    config: RunnableConfig = {"callbacks": [handler]}

    web_tool = create_web_search_tool(search_client=dummy_web_search)
    content, artifact = web_tool.invoke({"query": "zoom client update"}, config=config)

    assert len(handler.tool_starts) >= 1
    assert len(handler.tool_ends) >= 1
    assert "Web answer for zoom client update" in content


# ==============================================================================
# 3. Prompt CMS integration
# ==============================================================================

def test_fix_single_llm_uses_workflow_prompt_service():
    """Prompt nodes obtain prompts via get_workflow_prompt."""
    assert hasattr(engine, "get_workflow_prompt"), "engine must expose/use get_workflow_prompt"

    prompt = get_workflow_prompt("rag:router:llm")
    assert prompt is not None
    assert hasattr(prompt, "compile")
    assert hasattr(prompt, "version")

    rendered = prompt.compile(question="How to setup VPN?")
    assert "Question: How to setup VPN?" in rendered
    assert '{"route":"kb"}' in rendered


def test_fix_single_llm_nodes_compile_prompts_from_cms():
    """Verify OpenAIClassifierAdapter compiles prompt templates from Prompt CMS."""
    with patch("app.rag.classifiers.get_workflow_prompt") as mock_get_prompt:
        mock_p = FallbackPrompt("rag:router:llm", "Test router prompt for: {{question}}")
        mock_get_prompt.return_value = mock_p

        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(route="kb")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_router
        adapter = OpenAIClassifierAdapter(chat_model=mock_llm)

        res = adapter.route("MFA issue")

        mock_get_prompt.assert_called_with("rag:router:llm")
        mock_router.invoke.assert_called_once_with(
            "Test router prompt for: MFA issue",
            config={"metadata": {"prompt_name": "rag:router:llm", "prompt_fallback": True}},
        )
        assert res.route == "kb"


class MockActivePrompt:
    """Mock representing an active (non-fallback) Langfuse prompt client."""

    def __init__(self, name: str, version: int = 2):
        self.name = name
        self.version = version
        self.is_fallback = False

    def compile(self, **kwargs) -> str:
        return f"compiled-{self.name}"


@pytest.mark.asyncio
async def test_fix_single_llm_nodes_link_active_langfuse_prompt():
    """Verify active Langfuse prompt objects are linked in RunnableConfig metadata."""
    active_prompt = MockActivePrompt("rag:router:llm")

    # 1. route
    with patch("app.rag.classifiers.get_workflow_prompt", return_value=active_prompt):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(route="kb")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_router
        adapter = OpenAIClassifierAdapter(chat_model=mock_llm)

        adapter.route("test")
        mock_router.invoke.assert_called_once_with(
            "compiled-rag:router:llm",
            config={"metadata": {"langfuse_prompt": active_prompt}},
        )

    # 2. grade_kb
    kb_prompt = MockActivePrompt("rag:grade_kb:llm")
    with patch("app.rag.classifiers.get_workflow_prompt", return_value=kb_prompt):
        mock_grader = MagicMock()
        mock_grader.invoke.return_value = MagicMock(grade="good")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_grader
        adapter = OpenAIClassifierAdapter(chat_model=mock_llm)

        adapter.grade_kb("q", "q", [])
        mock_grader.invoke.assert_called_once_with(
            "compiled-rag:grade_kb:llm",
            config={"metadata": {"langfuse_prompt": kb_prompt}},
        )

    # 3. grade_web
    web_prompt = MockActivePrompt("rag:grade_web:llm")
    with patch("app.rag.classifiers.get_workflow_prompt", return_value=web_prompt):
        mock_grader = MagicMock()
        mock_grader.invoke.return_value = MagicMock(grade="good")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_grader
        adapter = OpenAIClassifierAdapter(chat_model=mock_llm)

        adapter.grade_web("q", "q", "res")
        mock_grader.invoke.assert_called_once_with(
            "compiled-rag:grade_web:llm",
            config={"metadata": {"langfuse_prompt": web_prompt}},
        )

    # 4. rewrite_query
    rewrite_prompt = MockActivePrompt("rag:rewrite_query")
    with patch("app.rag.engine.get_workflow_prompt", return_value=rewrite_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="new query"))
        mock_classifier = MagicMock()
        mock_classifier.route.return_value = MagicMock(route="kb")
        mock_classifier.grade_kb.side_effect = [MagicMock(grade="weak"), MagicMock(grade="good")]
        mock_classifier.grade_web.return_value = MagicMock(grade="weak")

        kb_tool = MagicMock()
        kb_tool.ainvoke = AsyncMock(return_value=("kb content", {"documents": [], "citations": []}))
        web_tool = MagicMock()
        web_tool.ainvoke = AsyncMock(return_value=("web content", {"retrieved_contexts": [], "citations": []}))

        graph = create_agentic_rag_graph(mock_classifier, model=mock_llm, kb_tool=kb_tool, web_tool=web_tool)
        await graph.ainvoke({
            "question": "q",
            "current_query": "",
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "retry_count": 0,
            "trace": [],
            "citations": [],
            "retrieved_contexts": [],
        })
        rewrite_call = [
            c for c in mock_llm.ainvoke.call_args_list
            if c[1].get("config", {}).get("metadata", {}).get("langfuse_prompt") == rewrite_prompt
        ]
        assert len(rewrite_call) >= 1

    # 5. generate_from_kb
    gen_kb_prompt = MockActivePrompt("rag:generate_kb")
    with patch("app.rag.engine.get_workflow_prompt", return_value=gen_kb_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        mock_classifier = MagicMock()
        mock_classifier.route.return_value = MagicMock(route="kb")
        mock_classifier.grade_kb.return_value = MagicMock(grade="good")

        kb_tool = MagicMock()
        kb_tool.ainvoke = AsyncMock(return_value=("kb content", {"documents": [], "citations": []}))

        graph = create_agentic_rag_graph(mock_classifier, model=mock_llm, kb_tool=kb_tool)
        await graph.ainvoke({
            "question": "q",
            "current_query": "",
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "retry_count": 0,
            "trace": [],
            "citations": [],
            "retrieved_contexts": [],
        })
        assert mock_llm.ainvoke.call_count == 1
        args, kwargs = mock_llm.ainvoke.call_args
        assert args[0] == "compiled-rag:generate_kb"
        assert kwargs["config"]["metadata"]["langfuse_prompt"] == gen_kb_prompt

    # 6. generate_from_web
    gen_web_prompt = MockActivePrompt("rag:generate_web")
    with patch("app.rag.engine.get_workflow_prompt", return_value=gen_web_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        mock_classifier = MagicMock()
        mock_classifier.route.return_value = MagicMock(route="kb")
        mock_classifier.grade_kb.return_value = MagicMock(grade="weak")
        mock_classifier.grade_web.return_value = MagicMock(grade="good")

        kb_tool = MagicMock()
        kb_tool.ainvoke = AsyncMock(return_value=("kb content", {"documents": [], "citations": []}))
        web_tool = MagicMock()
        web_tool.ainvoke = AsyncMock(return_value=("web content", {"retrieved_contexts": [], "citations": []}))

        graph = create_agentic_rag_graph(mock_classifier, model=mock_llm, kb_tool=kb_tool, web_tool=web_tool)
        await graph.ainvoke({
            "question": "q",
            "current_query": "",
            "kb_docs": [],
            "web_results": "res",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "retry_count": 0,
            "trace": [],
            "citations": [],
            "retrieved_contexts": [],
        })
        assert mock_llm.ainvoke.call_count == 1
        args, kwargs = mock_llm.ainvoke.call_args
        assert args[0] == "compiled-rag:generate_web"
        assert kwargs["config"]["metadata"]["langfuse_prompt"] == gen_web_prompt

    # 7. direct_answer
    direct_prompt = MockActivePrompt("rag:direct_answer")
    with patch("app.rag.engine.get_workflow_prompt", return_value=direct_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        await engine.direct_answer({"question": "q"}, model=mock_llm)
        mock_llm.ainvoke.assert_called_once_with(
            "compiled-rag:direct_answer",
            config={"metadata": {"langfuse_prompt": direct_prompt}},
        )
