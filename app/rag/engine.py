import logging
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langgraph.graph import END, START, StateGraph
import structlog

from app.core.concurrency import wrap_safe_invoke
from app.core.config import get_settings
from app.core.telemetry import extract_trace_id
from app.rag.classifiers import WorkflowClassifier, _build_prompt_config
from app.rag.state import AgentState
from app.rag.tools import create_retriever_tool, create_web_search_tool
from app.rag.vectorstore import get_retriever
from app.services.prompts import get_workflow_prompt

logger = logging.getLogger(__name__)

GENERATION_NODES: set[str] = {"generate_from_kb", "generate_from_web", "direct_answer"}

_llm = None
_web_search = None
_COMPILED_GRAPHS: dict[str, Any] = {}

__all__ = [
    "GENERATION_NODES",
    "create_agentic_rag_graph",
    "ask",
]


def _unpack_tool_output(raw_output: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(raw_output, tuple) and len(raw_output) == 2:
        content, artifact = raw_output
    elif hasattr(raw_output, "artifact") and getattr(raw_output, "artifact") is not None:
        content, artifact = getattr(raw_output, "content", ""), getattr(raw_output, "artifact")
    else:
        content, artifact = str(raw_output), {}

    if not isinstance(content, str):
        content = str(content)
    if not isinstance(artifact, dict):
        artifact = {}
    return content, artifact


def llm():
    global _llm
    if _llm is None:
        settings = get_settings()
        _llm = ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            temperature=0,
            stream_usage=True,
        )
    return _llm


def _default_web_search_tool():
    global _web_search
    if _web_search is None:
        settings = get_settings()
        api_key = settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else ""
        _web_search = TavilySearch(
            tavily_api_key=api_key,
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )
    return _web_search


web_search_tool = _default_web_search_tool


def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


def retrieve_kb(state: AgentState, config: RunnableConfig | None = None, retriever: Any = None):
    structlog.contextvars.bind_contextvars(step="retrieve_kb")
    active_retriever = retriever if retriever is not None else get_retriever()
    docs = active_retriever.invoke(state["current_query"], config=config)
    docs = docs or []
    citations = []
    seen = set()
    for d in docs:
        src = d.metadata.get("source", "Private KB") if hasattr(d, "metadata") and d.metadata else "Private KB"
        title = src.split("/")[-1].split("\\")[-1]
        if title not in seen:
            seen.add(title)
            citations.append({"title": title, "url": "", "type": "private_kb"})
    return {
        "kb_docs": docs,
        "retrieved_contexts": [d.page_content for d in docs] if docs else [],
        "citations": citations,
        "trace": [f"Private KB retrieval → {len(docs)} chunks"],
    }


def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


def search_web(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="search_web")
    result = web_search_tool().invoke({"query": state["current_query"]}, config=config)
    lines: list[str] = []
    citations: list[dict[str, Any]] = []
    web_contexts: list[str] = []
    if isinstance(result, dict):
        if result.get("answer"):
            lines.append("Search answer: " + result["answer"])
        for item in result.get("results", []):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"Title: {title}\nURL: {url}\nContent: {content}")
            citations.append({"title": title or url, "url": url, "type": "web"})
            web_contexts.append(f"Title: {title}\nURL: {url}\nContent: {content}")
    elif isinstance(result, str):
        lines.append(result)
        if result:
            web_contexts.append(result)
    elif result is not None:
        lines.append(str(result))
        web_contexts.append(str(result))
    return {
        "web_results": "\n\n".join(lines),
        "citations": citations,
        "source_used": "web",
        "retrieved_contexts": web_contexts,
        "trace": ["Web fallback → Tavily search"],
    }


def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < get_settings().max_retries:
        return "rewrite_query"
    return "insufficient"


async def rewrite_query(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="rewrite_query")
    prompt_obj = get_workflow_prompt("rag:rewrite_query")
    prompt = prompt_obj.compile(
        question=state["question"],
        current_query=state["current_query"],
    )
    run_cfg = _build_prompt_config(prompt_obj, config)
    response = await llm().ainvoke(prompt, config=run_cfg)
    rewritten = (response.content if hasattr(response, "content") else str(response)).strip()
    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "trace": [f"Query rewrite → {rewritten}"],
    }


async def generate_from_kb(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="generate_from_kb")
    context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
    prompt_obj = get_workflow_prompt("rag:generate_kb")
    prompt = prompt_obj.compile(
        question=state["question"],
        context=context,
    )
    run_cfg = _build_prompt_config(prompt_obj, config)
    response = await llm().ainvoke(prompt, config=run_cfg)
    answer = response.content if hasattr(response, "content") else str(response)
    citations = state.get("citations")
    if not citations:
        citations = []
        seen = set()
        for d in state["kb_docs"]:
            src = d.metadata.get("source", "Private KB")
            if src not in seen:
                seen.add(src)
                citations.append({"title": src.split("/")[-1], "url": "", "type": "private_kb"})
    retrieved_contexts = state.get("retrieved_contexts")
    if retrieved_contexts is None:
        retrieved_contexts = [d.page_content for d in state.get("kb_docs", [])]
    return {
        "answer": answer,
        "source_used": "vectorstore",
        "citations": citations,
        "retrieved_contexts": retrieved_contexts,
        "trace": ["Answer generation → PRIVATE KB"],
    }


async def generate_from_web(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="generate_from_web")
    prompt_obj = get_workflow_prompt("rag:generate_web")
    prompt = prompt_obj.compile(
        question=state["question"],
        web_results=state["web_results"],
    )
    run_cfg = _build_prompt_config(prompt_obj, config)
    response = await llm().ainvoke(prompt, config=run_cfg)
    answer = response.content if hasattr(response, "content") else str(response)
    return {
        "answer": answer,
        "source_used": "web_search",
        "retrieved_contexts": state.get("retrieved_contexts", []),
        "citations": state.get("citations", []),
        "trace": ["Answer generation → WEB SEARCH"],
    }


async def direct_answer(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="direct_answer")
    prompt_obj = get_workflow_prompt("rag:direct_answer")
    prompt = prompt_obj.compile(question=state["question"])
    run_cfg = _build_prompt_config(prompt_obj, config)
    response = await llm().ainvoke(prompt, config=run_cfg)
    answer = response.content if hasattr(response, "content") else str(response)
    return {
        "answer": answer,
        "source_used": "direct",
        "retrieved_contexts": [],
        "trace": ["Direct response → no retrieval"],
    }


def insufficient(state: AgentState):
    structlog.contextvars.bind_contextvars(step="insufficient")
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "retrieved_contexts": [],
        "trace": ["Stopped → insufficient reliable evidence"],
    }


def create_agentic_rag_graph(
    classifier: WorkflowClassifier,
    retriever: Any = None,
    *,
    kb_tool: BaseTool | None = None,
    web_tool: BaseTool | None = None,
) -> Any:
    """Build and compile the canonical 10-node StateGraph topology.

    Args:
        classifier: The WorkflowClassifier adapter driving routing and evidence grading.
        retriever: Optional legacy retriever object for backward compatibility.
            Wrapped via create_retriever_tool(retriever=retriever) if kb_tool is omitted.
        kb_tool: Optional canonical BaseTool instance for knowledge base retrieval.
            If omitted, defaults to create_retriever_tool().
        web_tool: Optional canonical BaseTool instance for web search fallback.
            If omitted, defaults to create_web_search_tool().

    Returns:
        Compiled StateGraph runnable ready for invoke/ainvoke execution.
    """
    if kb_tool is not None:
        active_kb_tool = kb_tool
    elif retriever is not None:
        active_kb_tool = create_retriever_tool(retriever=retriever)
    else:
        active_kb_tool = create_retriever_tool()

    if web_tool is not None:
        active_web_tool = web_tool
    else:
        active_web_tool = create_web_search_tool()

    def node_route_question(state: AgentState, config: RunnableConfig | None = None):
        structlog.contextvars.bind_contextvars(step="route_question")
        decision = classifier.route(state["question"], config=config)
        return {
            "current_query": state["question"],
            "source_used": decision.route,
            "trace": [f"Router → {decision.route.upper()}"],
        }

    async def node_retrieve_kb(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        raw_output = await active_kb_tool.ainvoke({"query": state["current_query"]}, config=config)
        content, artifact = _unpack_tool_output(raw_output)

        docs = artifact.get("documents", [])
        citations = artifact.get("citations", [])
        retrieved_contexts = [d.page_content for d in docs] if docs else ([content] if content else [])

        return {
            "kb_docs": docs,
            "retrieved_contexts": retrieved_contexts,
            "citations": citations,
            "trace": [f"Private KB retrieval → {len(docs)} chunks"],
        }

    def node_grade_kb(state: AgentState, config: RunnableConfig | None = None):
        structlog.contextvars.bind_contextvars(step="grade_kb")
        grade = classifier.grade_kb(
            question=state["question"],
            current_query=state["current_query"],
            kb_docs=state["kb_docs"],
            config=config,
        )
        return {
            "kb_grade": grade.grade,
            "trace": [f"KB evidence grade → {grade.grade.upper()}"],
        }

    async def node_search_web(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="search_web")
        raw_output = await active_web_tool.ainvoke({"query": state["current_query"]}, config=config)
        content, artifact = _unpack_tool_output(raw_output)

        citations = artifact.get("citations", [])
        contexts = artifact.get("retrieved_contexts", [])
        if not contexts and content:
            contexts = [content]

        return {
            "web_results": content,
            "citations": citations,
            "source_used": "web",
            "retrieved_contexts": contexts,
            "trace": ["Web fallback → Tavily search"],
        }

    def node_grade_web(state: AgentState, config: RunnableConfig | None = None):
        structlog.contextvars.bind_contextvars(step="grade_web")
        grade = classifier.grade_web(
            question=state["question"],
            current_query=state["current_query"],
            web_results=state["web_results"],
            config=config,
        )
        return {
            "web_grade": grade.grade,
            "trace": [f"Web evidence grade → {grade.grade.upper()}"],
        }

    graph = StateGraph(AgentState)
    nodes = {
        "route_question": node_route_question,
        "retrieve_kb": node_retrieve_kb,
        "grade_kb": node_grade_kb,
        "search_web": node_search_web,
        "grade_web": node_grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": direct_answer,
        "insufficient": insufficient,
    }
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "route_question")
    graph.add_conditional_edges("route_question", route_after_router, {
        "retrieve_kb": "retrieve_kb", "direct_answer": "direct_answer"
    })
    graph.add_edge("retrieve_kb", "grade_kb")
    graph.add_conditional_edges("grade_kb", after_kb, {
        "generate_from_kb": "generate_from_kb", "search_web": "search_web"
    })
    graph.add_edge("search_web", "grade_web")
    graph.add_conditional_edges("grade_web", after_web, {
        "generate_from_web": "generate_from_web",
        "rewrite_query": "rewrite_query",
        "insufficient": "insufficient",
    })
    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("insufficient", END)

    compiled = graph.compile()
    return wrap_safe_invoke(compiled)


def _get_workflow_graph(workflow: str) -> Any:
    if workflow not in _COMPILED_GRAPHS:
        if workflow == "fix_single_llm":
            from app.rag.classifiers import OpenAIClassifierAdapter
            _COMPILED_GRAPHS[workflow] = create_agentic_rag_graph(OpenAIClassifierAdapter())
        elif workflow == "fix_single_jev":
            from app.rag.classifiers import JevClassifierAdapter
            _COMPILED_GRAPHS[workflow] = create_agentic_rag_graph(JevClassifierAdapter())
        else:
            raise ValueError(f"Unknown workflow: {workflow}")
    return _COMPILED_GRAPHS[workflow]


def ask(
    question: str,
    workflow: str = "fix_single_llm",
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    callback: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Unified entrypoint to query an agentic RAG workflow graph."""
    target_graph = _get_workflow_graph(workflow)

    context: dict[str, Any] = {"workflow": workflow}
    if session_id:
        context["session_id"] = session_id
    if user_id:
        context["user_id"] = user_id

    extracted_tid = (
        extract_trace_id(callback)
        or (metadata.get("trace_id") if isinstance(metadata, dict) else None)
        or structlog.contextvars.get_contextvars().get("trace_id")
    )
    if extracted_tid:
        context["trace_id"] = extracted_tid

    structlog.contextvars.bind_contextvars(**context)

    initial: AgentState = {
        "question": question,
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
    }
    if extracted_tid:
        initial["trace_id"] = extracted_tid

    config: dict[str, Any] = {}
    if callback:
        config["callbacks"] = [callback]
    tags = kwargs.get("tags")
    if tags:
        config["tags"] = tags
    if metadata:
        config["metadata"] = metadata

    result = target_graph.invoke(initial, config=config if config else None)
    if not isinstance(result, dict):
        result = {"answer": str(result)}

    if extracted_tid and "trace_id" not in result:
        result["trace_id"] = extracted_tid

    return result
