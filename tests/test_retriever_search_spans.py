import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.rag import fix_single_llm, fix_single_jev
from app.services.prompts import FallbackPrompt, LOCAL_PROMPTS, get_workflow_prompt


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
# 1. Zero-coupling contract between fix_single_llm and fix_single_jev
# ==============================================================================

def test_strict_zero_coupling_between_llm_and_jev():
    """Verify standalone file boundary: neither module imports the other directly or indirectly."""
    repo_root = Path(__file__).resolve().parent.parent
    llm_path = repo_root / "app" / "rag" / "fix_single_llm.py"
    jev_path = repo_root / "app" / "rag" / "fix_single_jev.py"

    for path, forbidden in [(llm_path, "fix_single_jev"), (jev_path, "fix_single_llm")]:
        source = path.read_text(encoding="utf-8")
        parsed = ast.parse(source, filename=str(path))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert forbidden not in alias.name, f"{path.name} must not import {forbidden}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert forbidden not in module, f"{path.name} must not import from {forbidden}"
                for alias in node.names:
                    assert forbidden not in alias.name, f"{path.name} must not import {forbidden}"


# ==============================================================================
# 2. fix_single_llm: retrieve_kb and search_web config propagation & callbacks
# ==============================================================================

def test_fix_single_llm_retrieve_kb_signature_and_config_forwarding():
    """retrieve_kb accepts config: RunnableConfig | None and passes it to get_retriever().invoke."""
    sig = inspect.signature(fix_single_llm.retrieve_kb)
    assert "config" in sig.parameters, "retrieve_kb must accept a config parameter"

    mock_retriever = MagicMock()
    mock_docs = [Document(page_content="VPN info", metadata={"source": "kb/vpn.md"})]
    mock_retriever.invoke.return_value = mock_docs

    custom_config: RunnableConfig = {"tags": ["unit-test-llm"], "metadata": {"trace_id": "trace-123"}}
    state = {"current_query": "how do I connect to VPN?", "question": "how to vpn"}

    with patch.object(fix_single_llm, "get_retriever", return_value=mock_retriever):
        result = fix_single_llm.retrieve_kb(state, config=custom_config)

    mock_retriever.invoke.assert_called_once_with("how do I connect to VPN?", config=custom_config)
    assert result["kb_docs"] == mock_docs
    assert "Private KB retrieval → 1 chunks" in result["trace"][0]


def test_fix_single_llm_retrieve_kb_default_config_none():
    """retrieve_kb defaults config to None when called without config."""
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = []
    state = {"current_query": "mfa setup", "question": "mfa"}

    with patch.object(fix_single_llm, "get_retriever", return_value=mock_retriever):
        result = fix_single_llm.retrieve_kb(state)

    mock_retriever.invoke.assert_called_once_with("mfa setup", config=None)
    assert result["kb_docs"] == []


def test_fix_single_llm_retrieve_kb_triggers_retriever_callbacks():
    """retrieve_kb actually fires on_retriever_start and on_retriever_end via RunnableConfig."""
    handler = RecordingCallbackHandler()
    config: RunnableConfig = {"callbacks": [handler]}
    state = {"current_query": "password policy", "question": "password"}

    with patch.object(fix_single_llm, "get_retriever", return_value=DummyVectorRetriever()):
        result = fix_single_llm.retrieve_kb(state, config=config)

    assert len(handler.retriever_starts) == 1
    assert handler.retriever_starts[0] == "password policy"
    assert len(handler.retriever_ends) == 1
    assert handler.retriever_ends[0][0].page_content == "Content for password policy"


def test_fix_single_llm_search_web_signature_and_config_forwarding():
    """search_web accepts config: RunnableConfig | None and passes it to web_search_tool().invoke."""
    sig = inspect.signature(fix_single_llm.search_web)
    assert "config" in sig.parameters, "search_web must accept a config parameter"

    mock_search = MagicMock()
    mock_search.invoke.return_value = {
        "answer": "GlobalProtect setup guide",
        "results": [{"title": "VPN Guide", "url": "https://example.com/vpn", "content": "Install steps"}],
    }

    custom_config: RunnableConfig = {"tags": ["tavily-test"], "metadata": {"source": "web"}}
    state = {"current_query": "pulse secure setup", "question": "pulse secure"}

    with patch.object(fix_single_llm, "web_search_tool", return_value=mock_search):
        result = fix_single_llm.search_web(state, config=custom_config)

    mock_search.invoke.assert_called_once_with({"query": "pulse secure setup"}, config=custom_config)
    assert result["source_used"] == "web"
    assert len(result["citations"]) == 1
    assert "Search answer: GlobalProtect setup guide" in result["web_results"]


def test_fix_single_llm_search_web_default_config_none():
    """search_web defaults config to None when called without config."""
    mock_search = MagicMock()
    mock_search.invoke.return_value = {"results": []}
    state = {"current_query": "zoom client install", "question": "zoom"}

    with patch.object(fix_single_llm, "web_search_tool", return_value=mock_search):
        result = fix_single_llm.search_web(state)

    mock_search.invoke.assert_called_once_with({"query": "zoom client install"}, config=None)
    assert result["source_used"] == "web"


def test_fix_single_llm_search_web_triggers_tool_callbacks():
    """search_web fires on_tool_start and on_tool_end via RunnableConfig."""
    handler = RecordingCallbackHandler()
    config: RunnableConfig = {"callbacks": [handler]}
    state = {"current_query": "zoom client update", "question": "zoom"}

    with patch.object(fix_single_llm, "web_search_tool", return_value=dummy_web_search):
        result = fix_single_llm.search_web(state, config=config)

    assert len(handler.tool_starts) == 1
    assert len(handler.tool_ends) == 1
    assert result["source_used"] == "web"


# ==============================================================================
# 3. fix_single_jev: retrieve_kb and search_web config propagation & callbacks
# ==============================================================================

def test_fix_single_jev_retrieve_kb_signature_and_config_forwarding():
    """retrieve_kb in fix_single_jev accepts config and passes it to get_retriever().invoke."""
    sig = inspect.signature(fix_single_jev.retrieve_kb)
    assert "config" in sig.parameters, "retrieve_kb in fix_single_jev must accept a config parameter"

    mock_retriever = MagicMock()
    mock_docs = [Document(page_content="JEV KB doc", metadata={"source": "kb/jev.md"})]
    mock_retriever.invoke.return_value = mock_docs

    custom_config: RunnableConfig = {"tags": ["unit-test-jev"], "metadata": {"trace_id": "jev-trace-999"}}
    state = {"current_query": "jev query test", "question": "jev test"}

    with patch.object(fix_single_jev, "get_retriever", return_value=mock_retriever):
        result = fix_single_jev.retrieve_kb(state, config=custom_config)

    mock_retriever.invoke.assert_called_once_with("jev query test", config=custom_config)
    assert result["kb_docs"] == mock_docs
    assert "Private KB retrieval → 1 chunks" in result["trace"][0]


def test_fix_single_jev_retrieve_kb_default_config_none():
    """retrieve_kb in fix_single_jev defaults config to None when called without config."""
    mock_retriever = MagicMock()
    mock_retriever.invoke.return_value = []
    state = {"current_query": "query without config", "question": "query"}

    with patch.object(fix_single_jev, "get_retriever", return_value=mock_retriever):
        result = fix_single_jev.retrieve_kb(state)

    mock_retriever.invoke.assert_called_once_with("query without config", config=None)
    assert result["kb_docs"] == []


def test_fix_single_jev_retrieve_kb_triggers_retriever_callbacks():
    """retrieve_kb in fix_single_jev fires on_retriever_start and on_retriever_end via RunnableConfig."""
    handler = RecordingCallbackHandler()
    config: RunnableConfig = {"callbacks": [handler]}
    state = {"current_query": "wifi credentials", "question": "wifi"}

    with patch.object(fix_single_jev, "get_retriever", return_value=DummyVectorRetriever()):
        result = fix_single_jev.retrieve_kb(state, config=config)

    assert len(handler.retriever_starts) == 1
    assert handler.retriever_starts[0] == "wifi credentials"
    assert len(handler.retriever_ends) == 1


def test_fix_single_jev_search_web_signature_and_config_forwarding():
    """search_web in fix_single_jev accepts config and passes it to web_search_tool().invoke."""
    sig = inspect.signature(fix_single_jev.search_web)
    assert "config" in sig.parameters, "search_web in fix_single_jev must accept a config parameter"

    mock_search = MagicMock()
    mock_search.invoke.return_value = {
        "answer": "JEV web answer",
        "results": [{"title": "Web JEV", "url": "https://example.com/jev", "content": "JEV content"}],
    }

    custom_config: RunnableConfig = {"tags": ["jev-web-test"]}
    state = {"current_query": "jev web search", "question": "jev web"}

    with patch.object(fix_single_jev, "web_search_tool", return_value=mock_search):
        result = fix_single_jev.search_web(state, config=custom_config)

    mock_search.invoke.assert_called_once_with({"query": "jev web search"}, config=custom_config)
    assert result["source_used"] == "web"
    assert "Search answer: JEV web answer" in result["web_results"]


def test_fix_single_jev_search_web_default_config_none():
    """search_web in fix_single_jev defaults config to None when called without config."""
    mock_search = MagicMock()
    mock_search.invoke.return_value = {"results": []}
    state = {"current_query": "query web", "question": "query"}

    with patch.object(fix_single_jev, "web_search_tool", return_value=mock_search):
        result = fix_single_jev.search_web(state)

    mock_search.invoke.assert_called_once_with({"query": "query web"}, config=None)
    assert result["source_used"] == "web"


def test_fix_single_jev_search_web_triggers_tool_callbacks():
    """search_web in fix_single_jev fires on_tool_start and on_tool_end via RunnableConfig."""
    handler = RecordingCallbackHandler()
    config: RunnableConfig = {"callbacks": [handler]}
    state = {"current_query": "slack setup instructions", "question": "slack"}

    with patch.object(fix_single_jev, "web_search_tool", return_value=dummy_web_search):
        result = fix_single_jev.search_web(state, config=config)

    assert len(handler.tool_starts) == 1
    assert len(handler.tool_ends) == 1
    assert result["source_used"] == "web"


# ==============================================================================
# 4. Prompt CMS integration in fix_single_llm
# ==============================================================================

def test_fix_single_llm_uses_workflow_prompt_service():
    """fix_single_llm prompt nodes obtain prompts via get_workflow_prompt."""
    assert hasattr(fix_single_llm, "get_workflow_prompt"), "fix_single_llm must expose/use get_workflow_prompt"

    prompt = fix_single_llm.get_workflow_prompt("rag:router:llm")
    assert prompt is not None
    assert hasattr(prompt, "compile")
    assert hasattr(prompt, "version")

    rendered = prompt.compile(question="How to setup VPN?")
    assert "Question: How to setup VPN?" in rendered
    assert '{"route":"kb"}' in rendered


def test_fix_single_llm_nodes_compile_prompts_from_cms():
    """Verify nodes in fix_single_llm compile prompt templates from Prompt CMS."""
    # Test route_question uses get_workflow_prompt("rag:router:llm")
    with patch("app.rag.fix_single_llm.get_workflow_prompt") as mock_get_prompt:
        mock_p = FallbackPrompt("rag:router:llm", "Test router prompt for: {{question}}")
        mock_get_prompt.return_value = mock_p

        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(route="kb")
        with patch.object(fix_single_llm, "llm") as mock_llm_fn:
            mock_llm_fn.return_value.with_structured_output.return_value = mock_router
            state = {"question": "MFA issue"}
            res = fix_single_llm.route_question(state)

        mock_get_prompt.assert_called_with("rag:router:llm")
        mock_router.invoke.assert_called_once_with(
            "Test router prompt for: MFA issue",
            config={"metadata": {"prompt_name": "rag:router:llm", "prompt_fallback": True}},
        )
        assert res["source_used"] == "kb"


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
    """Verify active Langfuse prompt objects are linked in RunnableConfig metadata for fix_single_llm."""
    active_prompt = MockActivePrompt("rag:router:llm")

    # 1. route_question
    with patch("app.rag.fix_single_llm.get_workflow_prompt", return_value=active_prompt):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(route="kb")
        with patch.object(fix_single_llm, "llm") as mock_llm_fn:
            mock_llm_fn.return_value.with_structured_output.return_value = mock_router
            fix_single_llm.route_question({"question": "test"})
            mock_router.invoke.assert_called_once_with(
                "compiled-rag:router:llm",
                config={"metadata": {"langfuse_prompt": active_prompt}},
            )

    # 2. grade_kb_2
    kb_prompt = MockActivePrompt("rag:grade_kb:llm")
    with patch("app.rag.fix_single_llm.get_workflow_prompt", return_value=kb_prompt):
        mock_grader = MagicMock()
        mock_grader.invoke.return_value = MagicMock(grade="good")
        with patch.object(fix_single_llm, "llm") as mock_llm_fn:
            mock_llm_fn.return_value.with_structured_output.return_value = mock_grader
            fix_single_llm.grade_kb_2({"question": "q", "current_query": "q", "kb_docs": []})
            mock_grader.invoke.assert_called_once_with(
                "compiled-rag:grade_kb:llm",
                config={"metadata": {"langfuse_prompt": kb_prompt}},
            )

    # 3. grade_web_2
    web_prompt = MockActivePrompt("rag:grade_web:llm")
    with patch("app.rag.fix_single_llm.get_workflow_prompt", return_value=web_prompt):
        mock_grader = MagicMock()
        mock_grader.invoke.return_value = MagicMock(grade="good")
        with patch.object(fix_single_llm, "llm") as mock_llm_fn:
            mock_llm_fn.return_value.with_structured_output.return_value = mock_grader
            fix_single_llm.grade_web_2({"question": "q", "current_query": "q", "web_results": "res"})
            mock_grader.invoke.assert_called_once_with(
                "compiled-rag:grade_web:llm",
                config={"metadata": {"langfuse_prompt": web_prompt}},
            )

    # 4. rewrite_query_2
    rewrite_prompt = MockActivePrompt("rag:rewrite_query")
    with patch("app.rag.fix_single_llm.get_workflow_prompt", return_value=rewrite_prompt):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="new query")
        with patch.object(fix_single_llm, "llm", return_value=mock_llm):
            fix_single_llm.rewrite_query_2({"question": "q", "current_query": "q", "retry_count": 0})
            mock_llm.invoke.assert_called_once_with(
                "compiled-rag:rewrite_query",
                config={"metadata": {"langfuse_prompt": rewrite_prompt}},
            )

    # 5. generate_from_kb
    gen_kb_prompt = MockActivePrompt("rag:generate_kb")
    with patch("app.rag.fix_single_llm.get_workflow_prompt", return_value=gen_kb_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        with patch.object(fix_single_llm, "llm", return_value=mock_llm):
            await fix_single_llm.generate_from_kb({"question": "q", "kb_docs": []})
            mock_llm.ainvoke.assert_called_once_with(
                "compiled-rag:generate_kb",
                config={"metadata": {"langfuse_prompt": gen_kb_prompt}},
            )

    # 6. generate_from_web
    gen_web_prompt = MockActivePrompt("rag:generate_web")
    with patch("app.rag.fix_single_llm.get_workflow_prompt", return_value=gen_web_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        with patch.object(fix_single_llm, "llm", return_value=mock_llm):
            await fix_single_llm.generate_from_web({"question": "q", "web_results": "res"})
            mock_llm.ainvoke.assert_called_once_with(
                "compiled-rag:generate_web",
                config={"metadata": {"langfuse_prompt": gen_web_prompt}},
            )

    # 7. direct_answer
    direct_prompt = MockActivePrompt("rag:direct_answer")
    with patch("app.rag.fix_single_llm.get_workflow_prompt", return_value=direct_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        with patch.object(fix_single_llm, "llm", return_value=mock_llm):
            await fix_single_llm.direct_answer({"question": "q"})
            mock_llm.ainvoke.assert_called_once_with(
                "compiled-rag:direct_answer",
                config={"metadata": {"langfuse_prompt": direct_prompt}},
            )


@pytest.mark.asyncio
async def test_fix_single_jev_nodes_link_active_langfuse_prompt():
    """Verify active Langfuse prompt objects are linked in RunnableConfig metadata for fix_single_jev."""
    # 1. rewrite_query_2
    rewrite_prompt = MockActivePrompt("rag:rewrite_query")
    with patch("app.rag.fix_single_jev.get_workflow_prompt", return_value=rewrite_prompt):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="new query")
        with patch.object(fix_single_jev, "llm", return_value=mock_llm):
            fix_single_jev.rewrite_query_2({"question": "q", "current_query": "q", "retry_count": 0})
            mock_llm.invoke.assert_called_once_with(
                "compiled-rag:rewrite_query",
                config={"metadata": {"langfuse_prompt": rewrite_prompt}},
            )

    # 2. generate_from_kb
    gen_kb_prompt = MockActivePrompt("rag:generate_kb")
    with patch("app.rag.fix_single_jev.get_workflow_prompt", return_value=gen_kb_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        with patch.object(fix_single_jev, "llm", return_value=mock_llm):
            await fix_single_jev.generate_from_kb({"question": "q", "kb_docs": []})
            mock_llm.ainvoke.assert_called_once_with(
                "compiled-rag:generate_kb",
                config={"metadata": {"langfuse_prompt": gen_kb_prompt}},
            )

    # 3. generate_from_web
    gen_web_prompt = MockActivePrompt("rag:generate_web")
    with patch("app.rag.fix_single_jev.get_workflow_prompt", return_value=gen_web_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        with patch.object(fix_single_jev, "llm", return_value=mock_llm):
            await fix_single_jev.generate_from_web({"question": "q", "web_results": "res"})
            mock_llm.ainvoke.assert_called_once_with(
                "compiled-rag:generate_web",
                config={"metadata": {"langfuse_prompt": gen_web_prompt}},
            )

    # 4. direct_answer
    direct_prompt = MockActivePrompt("rag:direct_answer")
    with patch("app.rag.fix_single_jev.get_workflow_prompt", return_value=direct_prompt):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="answer"))
        with patch.object(fix_single_jev, "llm", return_value=mock_llm):
            await fix_single_jev.direct_answer({"question": "q"})
            mock_llm.ainvoke.assert_called_once_with(
                "compiled-rag:direct_answer",
                config={"metadata": {"langfuse_prompt": direct_prompt}},
            )

