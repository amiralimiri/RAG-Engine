import logging
from typing import Any, Literal, cast
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
import structlog

from app.core.config import get_settings
from app.rag.classifiers import OpenAIClassifierAdapter, _build_prompt_config
from app.rag.engine import (
    GENERATION_NODES,
    ask as engine_ask,
    create_agentic_rag_graph,
    llm as engine_llm,
    web_search_tool as engine_web_search,
)
from app.rag.state import AgentState, EvidenceGrade, RouteDecision
from app.rag.vectorstore import get_retriever
from app.services.prompts import get_workflow_prompt

logger = logging.getLogger(__name__)
settings = get_settings()

_llm = None
_web_search = None


def llm():
    global _llm
    if _llm is None:
        _llm = engine_llm()
    return _llm


def web_search_tool():
    global _web_search
    if _web_search is None:
        _web_search = engine_web_search()
    return _web_search


# Canonical 10-node StateGraph built via unified engine
classifier = OpenAIClassifierAdapter()
agent_graph = create_agentic_rag_graph(classifier)


def build_graph():
    return create_agentic_rag_graph(OpenAIClassifierAdapter())


def ask(
    question: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
    callback: Any | None = None,
) -> dict:
    target_graph = globals().get("agent_graph", agent_graph)
    return engine_ask(
        question,
        workflow="fix_single_llm",
        graph=target_graph,
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
        tags=tags,
        callback=callback,
    )


# Backward-compatibility node functions
def route_question(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="route_question")
    router = llm().with_structured_output(RouteDecision, method="json_mode")
    prompt_obj = get_workflow_prompt("rag:router:llm")
    prompt = prompt_obj.compile(question=state["question"])
    run_cfg = _build_prompt_config(prompt_obj, config)
    decision = cast(RouteDecision, router.invoke(prompt, config=run_cfg))
    return {
        "current_query": state["question"],
        "source_used": decision.route,
        "trace": [f"Router → {decision.route.upper()}"],
    }


def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


def retrieve_kb(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="retrieve_kb")
    docs = get_retriever().invoke(state["current_query"], config=config)
    return {
        "kb_docs": docs,
        "retrieved_contexts": [d.page_content for d in docs],
        "trace": [f"Private KB retrieval → {len(docs)} chunks"],
    }


def grade_kb_2(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="grade_kb")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    context = "\n\n".join(
        f"Source: {d.metadata.get('source', 'unknown')}\n{d.page_content}"
        for d in state["kb_docs"]
    )
    prompt_obj = get_workflow_prompt("rag:grade_kb:llm")
    prompt = prompt_obj.compile(
        question=state["question"],
        current_query=state["current_query"],
        context=context,
    )
    run_cfg = _build_prompt_config(prompt_obj, config)
    grade = cast(EvidenceGrade, grader.invoke(prompt, config=run_cfg))
    return {"kb_grade": grade.grade, "trace": [f"KB evidence grade → {grade.grade.upper()}"]}


grade_kb = grade_kb_2


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


def grade_web_2(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="grade_web")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    prompt_obj = get_workflow_prompt("rag:grade_web:llm")
    prompt = prompt_obj.compile(
        question=state["question"],
        current_query=state["current_query"],
        web_results=state["web_results"],
    )
    run_cfg = _build_prompt_config(prompt_obj, config)
    grade = cast(EvidenceGrade, grader.invoke(prompt, config=run_cfg))
    return {"web_grade": grade.grade, "trace": [f"Web evidence grade → {grade.grade.upper()}"]}


grade_web = grade_web_2


def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < settings.max_retries:
        return "rewrite_query"
    return "insufficient"


def rewrite_query_2(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="rewrite_query")
    prompt_obj = get_workflow_prompt("rag:rewrite_query")
    prompt = prompt_obj.compile(
        question=state["question"],
        current_query=state["current_query"],
    )
    run_cfg = _build_prompt_config(prompt_obj, config)
    rewritten = cast(str, llm().invoke(prompt, config=run_cfg).content).strip()
    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "trace": [f"Query rewrite → {rewritten}"],
    }


rewrite_query = rewrite_query_2


async def generate_from_kb(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="generate_from_kb")
    context = "\n\n".join(
        f"[Source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}"
        for d in state["kb_docs"]
    )
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
