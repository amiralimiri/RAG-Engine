import importlib
import logging
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langgraph.graph import END, START, StateGraph
import structlog

from app.core.concurrency import wrap_safe_invoke
from app.core.config import get_settings
from app.core.telemetry import extract_trace_id
from app.rag.classifiers import WorkflowClassifier
from app.rag.state import AgentState
from app.rag.vectorstore import get_retriever
from app.services.prompts import get_workflow_prompt

logger = logging.getLogger(__name__)

GENERATION_NODES: set[str] = {"generate_from_kb", "generate_from_web", "direct_answer"}

_llm = None
_web_search = None
_WORKFLOW_GRAPHS: dict[str, Any] = {}

__all__ = [
    "GENERATION_NODES",
    "create_agentic_rag_graph",
    "ask",
    "llm",
    "web_search_tool",
    "register_workflow_graph",
    "get_workflow_graph",
]


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


def web_search_tool():
    global _web_search
    if _web_search is None:
        settings = get_settings()
        _web_search = TavilySearch(
            tavily_api_key=settings.tavily_api_key.get_secret_value(),
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )
    return _web_search


def _build_prompt_config(prompt_obj: Any, config: RunnableConfig | None = None) -> RunnableConfig:
    """Build or merge a RunnableConfig linking prompt metadata to LangChain observations."""
    prompt_meta: dict[str, Any] = {}
    if prompt_obj is not None:
        if getattr(prompt_obj, "is_fallback", False) is True:
            prompt_meta = {
                "prompt_name": getattr(prompt_obj, "name", ""),
                "prompt_fallback": True,
            }
        else:
            prompt_meta = {"langfuse_prompt": prompt_obj}

    cfg: RunnableConfig = dict(config) if config else {}
    existing_meta = dict(cfg.get("metadata") or {})
    existing_meta.update(prompt_meta)
    cfg["metadata"] = existing_meta
    return cfg


def register_workflow_graph(workflow: str, graph: Any) -> None:
    """Register a workflow graph for unified invocation via ask()."""
    _WORKFLOW_GRAPHS[workflow] = graph


def get_workflow_graph(workflow: str) -> Any:
    """Resolve workflow graph from registry or dynamically load workflow shim."""
    if workflow in _WORKFLOW_GRAPHS:
        return _WORKFLOW_GRAPHS[workflow]

    if workflow in {"fix_single_llm", "fix_single_jev"}:
        try:
            mod = importlib.import_module(f"app.rag.{workflow}")
            graph = getattr(mod, "agent_graph", None)
            if graph is not None:
                return graph
        except Exception as e:
            logger.warning("Could not dynamically load workflow module %s: %s", workflow, e)

    raise ValueError(f"Unknown workflow or missing graph for: {workflow}")


def create_agentic_rag_graph(
    classifier: WorkflowClassifier,
    retriever: Any = None,
) -> Any:
    """Build and compile the canonical 10-node StateGraph topology."""

    def route_question(state: AgentState, config: RunnableConfig | None = None):
        structlog.contextvars.bind_contextvars(step="route_question")
        decision = classifier.route(state["question"], config=config)
        return {
            "current_query": state["question"],
            "source_used": decision.route,
            "trace": [f"Router → {decision.route.upper()}"],
        }

    def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
        return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"

    def retrieve_kb(state: AgentState, config: RunnableConfig | None = None):
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        active_retriever = retriever if retriever is not None else get_retriever()
        docs = active_retriever.invoke(state["current_query"], config=config)
        return {
            "kb_docs": docs,
            "retrieved_contexts": [d.page_content for d in docs],
            "trace": [f"Private KB retrieval → {len(docs)} chunks"],
        }

    def grade_kb(state: AgentState, config: RunnableConfig | None = None):
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

    def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
        return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"

    def search_web(state: AgentState, config: RunnableConfig | None = None):
        structlog.contextvars.bind_contextvars(step="search_web")
        result = web_search_tool().invoke({"query": state["current_query"]}, config=config)
        lines, citations = [], []
        web_contexts = []
        if isinstance(result, dict):
            if result.get("answer"):
                lines.append("Search answer: " + result["answer"])
            for item in result.get("results", []):
                title, url, content = item.get("title", ""), item.get("url", ""), item.get("content", "")
                lines.append(f"Title: {title}\nURL: {url}\nContent: {content}")
                citations.append({"title": title or url, "url": url, "type": "web"})
                web_contexts.append(f"Title: {title}\nURL: {url}\nContent: {content}")
        else:
            lines.append(str(result))
            web_contexts.append(str(result))
        return {
            "web_results": "\n\n".join(lines),
            "citations": citations,
            "source_used": "web",
            "retrieved_contexts": web_contexts,
            "trace": ["Web fallback → Tavily search"],
        }

    def grade_web(state: AgentState, config: RunnableConfig | None = None):
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

    def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
        if state["web_grade"] == "good":
            return "generate_from_web"
        if state["retry_count"] < get_settings().max_retries:
            return "rewrite_query"
        return "insufficient"

    def rewrite_query(state: AgentState, config: RunnableConfig | None = None):
        structlog.contextvars.bind_contextvars(step="rewrite_query")
        prompt_obj = get_workflow_prompt("rag:rewrite_query")
        prompt = prompt_obj.compile(
            question=state["question"],
            current_query=state["current_query"],
        )
        run_cfg = _build_prompt_config(prompt_obj, config)
        response = llm().invoke(prompt, config=run_cfg)
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

    graph = StateGraph(AgentState)
    for name, fn in {
        "route_question": route_question,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb,
        "search_web": search_web,
        "grade_web": grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": direct_answer,
        "insufficient": insufficient,
    }.items():
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


def ask(
    question: str,
    workflow: str = "fix_single_llm",
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
    callback: Any | None = None,
    graph: Any | None = None,
    classifier: WorkflowClassifier | None = None,
    retriever: Any = None,
) -> dict[str, Any]:
    """Unified entrypoint to query an agentic RAG workflow graph."""
    if graph is not None:
        target_graph = graph
    elif classifier is not None:
        target_graph = create_agentic_rag_graph(classifier, retriever=retriever)
    else:
        target_graph = get_workflow_graph(workflow)

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
    if tags:
        config["tags"] = tags
    if metadata:
        config["metadata"] = metadata

    result = target_graph.invoke(initial, config=config if config else None)
    if not isinstance(result, dict):
        result = {"answer": str(result)}

    result.setdefault("retrieved_contexts", [])
    result.setdefault("citations", [])
    result.setdefault("source_used", "")
    result.setdefault("trace", [])
    if extracted_tid:
        result.setdefault("trace_id", extracted_tid)

    return result
