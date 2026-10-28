import asyncio
from app.core.concurrency import wrap_safe_invoke
import logging
from typing import Any, Literal, cast
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langgraph.graph import StateGraph, START, END
import structlog
from app.core.config import get_settings
from app.core.telemetry import extract_trace_id
from app.rag.state import AgentState, RouteDecision, EvidenceGrade
from app.rag.vectorstore import get_retriever
from app.services.prompts import get_workflow_prompt

logger = logging.getLogger(__name__)
settings = get_settings()

_llm = None
_web_search = None


def llm():
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            temperature=0,
        )
    return _llm


def web_search_tool():
    global _web_search
    if _web_search is None:
        _web_search = TavilySearch(
            tavily_api_key=settings.tavily_api_key.get_secret_value(),
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )
    return _web_search


def route_question(state: AgentState):
    structlog.contextvars.bind_contextvars(step="route_question")
    router = llm().with_structured_output(RouteDecision, method="json_mode")
    prompt_obj = get_workflow_prompt("rag:router:llm")
    prompt = prompt_obj.compile(question=state["question"])
    decision = cast(RouteDecision, router.invoke(prompt))
    return {"current_query": state['question'], "source_used": decision.route, "trace": [f"Router → {decision.route.upper()}"] }


def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


def retrieve_kb(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="retrieve_kb")
    docs = get_retriever().invoke(state["current_query"], config=config)
    return {"kb_docs": docs, "trace": [f"Private KB retrieval → {len(docs)} chunks"] }


def grade_kb(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_kb")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    context = "\n\n".join(f"Source: {d.metadata.get('source','unknown')}\n{d.page_content}" for d in state["kb_docs"])
    prompt = f"""
You grade evidence for an enterprise IT support assistant.
Question: {state['question']}
Private company KB evidence:\n{context}
Return good only if the evidence is sufficient to answer confidently and specifically.
Otherwise return weak. JSON: {{"grade":"good"}} or {{"grade":"weak"}}.
"""
    grade = cast(EvidenceGrade, grader.invoke(prompt))
    return {"kb_grade": grade.grade, "trace": [f"KB evidence grade → {grade.grade.upper()}"] }


def grade_kb_2(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_kb")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    context = "\n\n".join(f"Source: {d.metadata.get('source','unknown')}\n{d.page_content}" for d in state["kb_docs"])
    prompt_obj = get_workflow_prompt("rag:grade_kb:llm")
    prompt = prompt_obj.compile(
        question=state["question"],
        current_query=state["current_query"],
        context=context,
    )
    grade = cast(EvidenceGrade, grader.invoke(prompt))
    return {"kb_grade": grade.grade, "trace": [f"KB evidence grade → {grade.grade.upper()}"] }

    
def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


def search_web(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="search_web")
    result = web_search_tool().invoke({"query": state["current_query"]}, config=config)
    lines, citations = [], []
    if isinstance(result, dict):
        if result.get("answer"):
            lines.append("Search answer: " + result["answer"])
        for item in result.get("results", []):
            title, url, content = item.get("title", ""), item.get("url", ""), item.get("content", "")
            lines.append(f"Title: {title}\nURL: {url}\nContent: {content}")
            citations.append({"title": title or url, "url": url, "type": "web"})
    else:
        lines.append(str(result))
    return {
        "web_results": "\n\n".join(lines),
        "citations": citations,
        "source_used": "web",
        "trace": ["Web fallback → Tavily search"],
    }


def grade_web(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_web")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    prompt = f"""
Question: {state['question']}
Web evidence:\n{state['web_results']}
Return good if the evidence is sufficient and directly relevant; otherwise weak.
Return valid JSON like {{"grade":"good"}}.
"""
    grade = cast(EvidenceGrade, grader.invoke(prompt))
    return {"web_grade": grade.grade, "trace": [f"Web evidence grade → {grade.grade.upper()}"]}


def grade_web_2(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_web")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    prompt_obj = get_workflow_prompt("rag:grade_web:llm")
    prompt = prompt_obj.compile(
        question=state["question"],
        current_query=state["current_query"],
        web_results=state["web_results"],
    )
    grade = cast(EvidenceGrade, grader.invoke(prompt))
    return {"web_grade": grade.grade, "trace": [f"Web evidence grade → {grade.grade.upper()}"]}

    

def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < settings.max_retries:
        return "rewrite_query"
    return "insufficient"

def rewrite_query(state: AgentState):
    structlog.contextvars.bind_contextvars(step="rewrite_query")
    prompt = f"""
Rewrite this IT support question for better private knowledge retrieval and vendor web search.
Preserve intent, add useful technical keywords, do not answer, return only the query.
Question: {state['question']}
""" 
    
    rewritten = cast(str, llm().invoke(prompt).content).strip()
    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "trace": [f"Query rewrite → {rewritten}"],
    }
    
    
def rewrite_query_2(state: AgentState):
    structlog.contextvars.bind_contextvars(step="rewrite_query")
    prompt_obj = get_workflow_prompt("rag:rewrite_query")
    prompt = prompt_obj.compile(
        question=state["question"],
        current_query=state["current_query"],
    )
    rewritten = cast(str, llm().invoke(prompt).content).strip()
    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "trace": [f"Query rewrite → {rewritten}"],
    }


GENERATION_NODES = {"generate_from_kb", "generate_from_web", "direct_answer"}


async def generate_from_kb(state: AgentState):
    structlog.contextvars.bind_contextvars(step="generate_from_kb")
    context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
    prompt_obj = get_workflow_prompt("rag:generate_kb")
    prompt = prompt_obj.compile(
        question=state["question"],
        context=context,
    )
    response = await llm().ainvoke(prompt)
    answer = response.content if hasattr(response, "content") else str(response)
    citations = []
    seen = set()
    for d in state["kb_docs"]:
        src = d.metadata.get("source", "Private KB")
        if src not in seen:
            seen.add(src)
            citations.append({"title": src.split("/")[-1], "url": "", "type": "private_kb"})
    return {"answer": answer, "source_used": "vectorstore", "citations": citations, "trace": ["Answer generation → PRIVATE KB"] }


async def generate_from_web(state: AgentState):
    structlog.contextvars.bind_contextvars(step="generate_from_web")
    prompt_obj = get_workflow_prompt("rag:generate_web")
    prompt = prompt_obj.compile(
        question=state["question"],
        web_results=state["web_results"],
    )
    response = await llm().ainvoke(prompt)
    answer = response.content if hasattr(response, "content") else str(response)
    return {"answer": answer, "source_used": "web_search", "trace": ["Answer generation → WEB SEARCH"] }


async def direct_answer(state: AgentState):
    structlog.contextvars.bind_contextvars(step="direct_answer")
    prompt_obj = get_workflow_prompt("rag:direct_answer")
    prompt = prompt_obj.compile(question=state["question"])
    response = await llm().ainvoke(prompt)
    answer = response.content if hasattr(response, "content") else str(response)
    return {"answer": answer, "source_used": "direct", "trace": ["Direct response → no retrieval"] }


def insufficient(state: AgentState):
    structlog.contextvars.bind_contextvars(step="insufficient")
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "trace": ["Stopped → insufficient reliable evidence"],
    }


def build_graph():
    graph = StateGraph(AgentState)
    for name, fn in {
        "route_question": route_question,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb_2,
        "search_web": search_web,
        "grade_web": grade_web_2,
        "rewrite_query": rewrite_query_2,
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
        "generate_from_web": "generate_from_web", "rewrite_query": "rewrite_query", "insufficient": "insufficient"
    })
    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("insufficient", END)
    compiled = graph.compile()
    return wrap_safe_invoke(compiled)


agent_graph = build_graph()


def ask(
    question: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
    callback: Any | None = None,
) -> dict:
    context: dict[str, Any] = {"workflow": "fix_single_llm"}
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
    return agent_graph.invoke(initial, config=config if config else None)