import logging
from typing import Any, Literal, cast
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langgraph.graph import StateGraph, START, END
from langchain_typesafe import Noul, Choice, TypeSafeClassifier
import structlog

from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client, extract_trace_id
from app.rag.state import AgentState, RouteDecision, EvidenceGrade
from app.rag.vectorstore import get_retriever

logger = logging.getLogger(__name__)
settings = get_settings()

_llm = None
_jev = None
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


def jev():
    global _jev
    if _jev is None:
        # beta
        _jev = TypeSafeClassifier(
            api_key=settings.jev_api_key,
            base_url=settings.jev_base_url,
            model=settings.jev_model
        )
    return _jev

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


def _estimate_jev_tokens(prompt_text: str, output_text: str) -> dict[str, int]:
    """Heuristic rule-of-thumb: 1 token ~= 4 characters."""
    input_tokens = max(1, len(prompt_text) // 4)
    output_tokens = max(1, len(output_text) // 4)
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total": input_tokens + output_tokens,
    }


def _record_jev_generation(
    name: str,
    input_text: str,
    output_text: str,
    trace_id: str | None = None,
) -> None:
    try:
        client = get_langfuse_client()
        if not client:
            return
        resolved_trace_id = trace_id or structlog.contextvars.get_contextvars().get("trace_id")
        trace_context = {"trace_id": resolved_trace_id} if resolved_trace_id else None
        usage = _estimate_jev_tokens(input_text, output_text)
        if hasattr(client, "start_observation"):
            obs_kwargs: dict[str, Any] = {
                "name": name,
                "as_type": "generation",
                "model": settings.jev_model,
                "input": input_text,
                "output": output_text,
                "usage_details": usage,
                "metadata": {"classifier": "typesafe_jev"},
            }
            if trace_context:
                obs_kwargs["trace_context"] = trace_context
            client.start_observation(**obs_kwargs).end()
        elif hasattr(client, "generation"):
            gen_kwargs: dict[str, Any] = {
                "name": name,
                "model": settings.jev_model,
                "input": input_text,
                "output": output_text,
                "usage": usage,
                "metadata": {"classifier": "typesafe_jev"},
            }
            if trace_id:
                gen_kwargs["trace_id"] = resolved_trace_id
            client.generation(**gen_kwargs)
    except Exception as exc:
        logger.debug("Failed to record JEV generation span: %s", exc)

########################## Node 1 — Route the Question ##########################

def route_question_llm(state: AgentState):
    structlog.contextvars.bind_contextvars(step="route_question")
    router = llm().with_structured_output(RouteDecision, method="json_mode")
    prompt = f"""
You route messages for an enterprise IT support assistant.
Use kb for questions about company IT policies, VPN, password reset, MFA, laptop setup,
software access, security, email, devices, troubleshooting, or technology support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
Question: {state['question']}
Return valid JSON like {{"route":"kb"}}.
"""
    decision = cast(RouteDecision, router.invoke(prompt))
    return {"current_query": state['question'], "source_used": decision.route, "trace": [f"Router → {decision.route.upper()}"] }


def route_question_jev(state: AgentState):
    structlog.contextvars.bind_contextvars(step="route_question")
    question = state["question"]

    result = jev().invoke({
        "state": question,
        "questions": {
            "route": Choice(
                instructions=(
                    "Which path should this enterprise IT support question take?"
                ),
                criteria={
                    "kb": (
                        "Questions about company IT policies, VPN, password reset, "
                        "MFA, laptop setup, software access, security, email, "
                        "devices, troubleshooting, or any technology support that "
                        "requires company knowledge."
                    ),
                    "direct": (
                        "Only greetings, thanks, or casual chat that needs no "
                        "company knowledge."
                    ),
                }
            )
        }
    })

    decision = result.choices["route"].choice
    trace_id = state.get("trace_id")
    _record_jev_generation("jev_router", question, decision, trace_id=trace_id)
    # confidence = result.choices["route"].confidence
    # print(f"[Router] {decision} (confidence: {confidence:.3f})")

    return {
        "current_query": state['question'],
        "source_used": decision,
        "trace": [f"Router → {decision.upper()}"],
    }
    
    
def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


########################## Node 2 — Retrieve from the Private KB ##########################

def retrieve_kb(state: AgentState):
    structlog.contextvars.bind_contextvars(step="retrieve_kb")
    docs = get_retriever().invoke(state["current_query"])
    return {"kb_docs": docs, "trace": [f"Private KB retrieval → {len(docs)} chunks"] }


########################## Node 3 — Grade Private KB Evidence ##########################

def grade_kb_llm(state: AgentState):
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


def grade_kb_jev(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_kb")
    context = "\n\n".join(
        f"Source: {d.metadata.get('source', 'unknown')}\n{d.page_content}"
        for d in state["kb_docs"]
    )
    prompt_state = (
        f"Question: {state['question']}\n\n"
        f"Private company KB evidence:\n{context}"
    )

    result = jev().invoke({
        "state": prompt_state,
        "questions": {
            "grade": Choice(
                instructions=(
                    "Is the provided private company KB evidence sufficient to "
                    "answer the question confidently and specifically?"
                ),
                criteria={
                    "good": (
                        "The evidence directly and specifically answers the "
                        "question, allowing a confident, concrete response."
                    ),
                    "weak": (
                        "The evidence is missing, incomplete, tangential, or too "
                        "vague to answer the question confidently and specifically."
                    ),
                }
            )
        }
    })

    grade = result.choices["grade"].choice
    trace_id = state.get("trace_id")
    _record_jev_generation("jev_kb_grader", prompt_state, grade, trace_id=trace_id)
    # confidence = result.choices["grade"].confidence
    # print(f"[KB Grader] {grade} (confidence: {confidence:.3f})")

    return {
        "kb_grade": grade,
        "trace": [f"KB evidence grade → {grade.upper()}"],
    }
    

def grade_kb_llm_2(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_kb")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    context = "\n\n".join(f"Source: {d.metadata.get('source','unknown')}\n{d.page_content}" for d in state["kb_docs"])
    prompt = f"""
You grade evidence for an enterprise IT support assistant.

Original user question:
{state['question']}

Query used for retrieval:
{state['current_query']}

Private company KB evidence:
{context}

Evaluate whether the evidence is sufficient to answer
the ORIGINAL user question confidently and specifically.

The retrieval query is only a search aid.
Do not grade the evidence as good merely because it
is relevant to the retrieval query.

Return good only if the evidence supports a sufficiently
specific answer to the original question.
Otherwise return weak.

Return valid JSON:
{{"grade": "good"}} or {{"grade": "weak"}}.
"""

    grade = cast(EvidenceGrade, grader.invoke(prompt))
    return {"kb_grade": grade.grade, "trace": [f"KB evidence grade → {grade.grade.upper()}"] }


def grade_kb_jev_2(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_kb")
    context = "\n\n".join(
        f"Source: {d.metadata.get('source', 'unknown')}\n{d.page_content}"
        for d in state["kb_docs"]
    )
    prompt_state = (
        f"Original user question:\n{state['question']}\n\n"
        f"Query used for retrieval (search aid only):\n{state['current_query']}\n\n"
        f"Private company KB evidence:\n{context}"
    )

    result = jev().invoke({
        "state": prompt_state,
        "questions": {
            "grade": Choice(
                instructions=(
                    "Is the provided private company KB evidence sufficient to "
                    "answer the ORIGINAL user question confidently and "
                    "specifically? Judge against the original question, not the "
                    "retrieval query — the retrieval query is only a search aid."
                ),
                criteria={
                    "good": (
                        "The evidence supports a sufficiently specific, "
                        "confident answer to the ORIGINAL user question."
                    ),
                    "weak": (
                        "The evidence is missing, incomplete, tangential, or only "
                        "relevant to the retrieval query — not enough to answer "
                        "the ORIGINAL user question specifically and confidently."
                    ),
                }
            )
        }
    })

    grade = result.choices["grade"].choice
    trace_id = state.get("trace_id")
    _record_jev_generation("jev_kb_grader", prompt_state, grade, trace_id=trace_id)
    # confidence = result.choices["grade"].confidence
    # print(f"[KB Grader] {grade} (confidence: {confidence:.3f})")

    return {
        "kb_grade": grade,
        "trace": [f"KB evidence grade → {grade.upper()}"],
    }
    
    
def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


########################## Node 4 — Tavily Web Search Fallback ##########################

def search_web(state: AgentState):
    structlog.contextvars.bind_contextvars(step="search_web")
    result = web_search_tool().invoke({"query": state["current_query"]})
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


########################## Node 5 — Grade Web Evidence ##########################

def grade_web_llm(state: AgentState):
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


def grade_web_jev(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_web")
    prompt_state = (
        f"Question: {state['question']}\n\n"
        f"Web evidence:\n{state['web_results']}"
    )
    result = jev().invoke({
        "state": prompt_state,
        "questions": {
            "grade": Choice(
                instructions=(
                    "Is the provided web evidence sufficient and directly "
                    "relevant to answer the question confidently?"
                ),
                criteria={
                    "good": (
                        "The web evidence is sufficient and directly relevant, "
                        "allowing a confident, specific answer to the question."
                    ),
                    "weak": (
                        "The web evidence is missing, incomplete, tangential, or "
                        "not directly relevant — not enough to answer the "
                        "question confidently."
                    ),
                }
            )
        }
    })

    grade = result.choices["grade"].choice
    trace_id = state.get("trace_id")
    _record_jev_generation("jev_web_grader", prompt_state, grade, trace_id=trace_id)
    # confidence = result.choices["grade"].confidence
    # print(f"[Web Grader] {grade} (confidence: {confidence:.3f})")

    return {
        "web_grade": grade,
        "trace": [f"Web evidence grade → {grade.upper()}"],
    }
    

def grade_web_llm_2(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_web")
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")

    prompt = f"""
You grade web evidence for an enterprise IT support assistant.

Original user question:
{state['question']}

Query used for web search:
{state['current_query']}

Web evidence:
{state['web_results']}

Evaluate whether the web evidence is sufficient,
relevant, and specific enough to answer the ORIGINAL
user question confidently.

The search query is only a retrieval aid.
Do not grade the evidence as good merely because
it matches the search query.

Use good only when the evidence directly supports
a sufficiently specific answer to the original question.
Otherwise, return weak.

Return valid JSON:
{{"grade": "good"}} or {{"grade": "weak"}}.
"""

    grade = cast(EvidenceGrade, grader.invoke(prompt))
    return {"web_grade": grade.grade, "trace": [f"Web evidence grade → {grade.grade.upper()}"]}


def grade_web_jev_2(state: AgentState):
    structlog.contextvars.bind_contextvars(step="grade_web")
    prompt_state = (
        f"Original user question:\n{state['question']}\n\n"
        f"Query used for web search (retrieval aid only):\n{state['current_query']}\n\n"
        f"Web evidence:\n{state['web_results']}"
    )
    result = jev().invoke({
        "state": prompt_state,
        "questions": {
            "grade": Choice(
                instructions=(
                    "Is the provided web evidence sufficient, relevant, and "
                    "specific enough to answer the ORIGINAL user question "
                    "confidently? Judge against the original question, not the "
                    "search query — the search query is only a retrieval aid."
                ),
                criteria={
                    "good": (
                        "The web evidence directly supports a sufficiently "
                        "specific, confident answer to the ORIGINAL user "
                        "question."
                    ),
                    "weak": (
                        "The web evidence is missing, incomplete, tangential, or "
                        "only matches the search query — not enough to answer "
                        "the ORIGINAL user question specifically and confidently."
                    ),
                }
            )
        }
    })

    grade = result.choices["grade"].choice
    trace_id = state.get("trace_id")
    _record_jev_generation("jev_web_grader", prompt_state, grade, trace_id=trace_id)
    # confidence = result.choices["grade"].confidence
    # print(f"[Web Grader] {grade} (confidence: {confidence:.3f})")

    return {
        "web_grade": grade,
        "trace": [f"Web evidence grade → {grade.upper()}"],
    }
    
    
def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < settings.max_retries:
        return "rewrite_query"
    return "insufficient"


########################## Node 6 — Rewrite the Query ##########################

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
    prompt = f"""
You improve search queries for an enterprise IT assistant.

Original user question:
{state['question']}

Previous query:
{state['current_query']}

Create a meaningfully improved query that preserves
the original intent and addresses the missing information.

Do not answer the question.
Return only the new query.
"""

    rewritten = cast(str, llm().invoke(prompt).content).strip()
    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "trace": [f"Query rewrite → {rewritten}"],
    }


########################## Node 7 — Generate from Private KB ##########################

def generate_from_kb(state: AgentState):
    structlog.contextvars.bind_contextvars(step="generate_from_kb")
    context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
    answer = llm().invoke(f"""
You are an enterprise IT support copilot. Answer ONLY from the private company KB below.
Be concise, actionable, and safe. If steps are present, present them clearly.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
Question: {state['question']}\n\nPrivate KB:\n{context}
""").content
    citations = []
    seen = set()
    for d in state["kb_docs"]:
        src = d.metadata.get("source", "Private KB")
        if src not in seen:
            seen.add(src)
            citations.append({"title": src.split("/")[-1], "url": "", "type": "private_kb"})
    return {"answer": answer, "source_used": "private_kb", "citations": citations, "trace": ["Answer generation → PRIVATE KB"] }


########################## Node 8 — Generate from Web Search ##########################

def generate_from_web(state: AgentState):
    structlog.contextvars.bind_contextvars(step="generate_from_web")
    answer = llm().invoke(f"""
You are an enterprise IT support copilot. The private company KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external web information and may need IT validation before changing company-managed systems.
Question: {state['question']}\n\nWeb evidence:\n{state['web_results']}
""").content
    return {"answer": answer, "source_used": "web_search", "trace": ["Answer generation → WEB SEARCH"] }


########################## Node 9 — Direct Answer ##########################

def direct_answer(state: AgentState):
    structlog.contextvars.bind_contextvars(step="direct_answer")
    answer = llm().invoke(f"Respond briefly and naturally to: {state['question']}").content
    return {"answer": answer, "source_used": "direct", "trace": ["Direct response → no retrieval"] }


########################## Node 10 — Insufficient Evidence Answer ##########################

def insufficient(state: AgentState):
    structlog.contextvars.bind_contextvars(step="insufficient")
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "trace": ["Stopped → insufficient reliable evidence"],
    }


########################## Build the LangGraph Workflow ##########################

def build_graph():
    graph = StateGraph(AgentState)
    for name, fn in {
        "route_question": route_question_jev,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb_jev_2,
        "search_web": search_web,
        "grade_web": grade_web_jev_2,
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
    return graph.compile()


agent_graph = build_graph()


########################## Helper Function ##########################

def ask(
    question: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
    callback: Any | None = None,
) -> dict:
    context: dict[str, Any] = {"workflow": "fix_single_jev"}
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