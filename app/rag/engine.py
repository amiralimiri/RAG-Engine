import logging
from collections.abc import Sequence
from typing import Any, Literal

import structlog
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.core.concurrency import wrap_safe_invoke
from app.core.config import get_settings
from app.rag.classifiers import WorkflowClassifier, _build_prompt_config
from app.rag.state import AgentState
from app.rag.tools import create_retriever_tool, create_web_search_tool
from app.services.prompts import get_workflow_prompt

logger = logging.getLogger(__name__)

GENERATION_NODES: set[str] = {"generate_from_kb", "generate_from_web", "direct_answer"}

__all__ = [
    "GENERATION_NODES",
    "get_chat_model",
    "create_agentic_rag_graph",
    "route_start",
]


def _format_chat_history(messages: Any) -> str:
    if not messages or not isinstance(messages, Sequence):
        return ""
    lines: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            role = "Human"
            content = m.content
        elif isinstance(m, AIMessage):
            role = "Assistant"
            content = m.content
        elif isinstance(m, SystemMessage):
            role = "System"
            content = m.content
        elif isinstance(m, dict):
            role = (m.get("role") or "User").capitalize()
            content = m.get("content", "")
        else:
            role = getattr(m, "type", getattr(m, "role", "Message")).capitalize()
            content = getattr(m, "content", str(m))
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


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


def get_chat_model() -> BaseChatModel:
    """Instantiate a stateless ChatOpenAI model configured with application settings."""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
        stream_usage=True,
    )


def route_start(state: AgentState) -> Literal["condense_query", "route_question"]:
    messages = state.get("messages") or []
    if len(messages) > 1:
        return "condense_query"
    return "route_question"


def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < get_settings().max_retries:
        return "rewrite_query"
    return "insufficient"


async def direct_answer(
    state: AgentState,
    config: RunnableConfig | None = None,
    *,
    model: BaseChatModel | None = None,
) -> dict[str, Any]:
    structlog.contextvars.bind_contextvars(step="direct_answer")
    prompt_obj = get_workflow_prompt("rag:direct_answer")
    raw_history = state.get("messages", [])[:-1] if state.get("messages") else []
    if raw_history:
        history_str = _format_chat_history(raw_history)
        try:
            prompt = prompt_obj.compile(
                question=state["question"],
                history=history_str,
                chat_history=history_str,
            )
        except TypeError:
            prompt = prompt_obj.compile(question=state["question"])
    else:
        prompt = prompt_obj.compile(question=state["question"])
    run_cfg = _build_prompt_config(prompt_obj, config)
    active_model = model if model is not None else get_chat_model()
    response = await active_model.ainvoke(prompt, config=run_cfg)
    answer = response.content if hasattr(response, "content") else str(response)
    return {
        "answer": answer,
        "source_used": "direct",
        "retrieved_contexts": [],
        "trace": ["Direct response → no retrieval"],
    }


def insufficient(state: AgentState) -> dict[str, Any]:
    structlog.contextvars.bind_contextvars(step="insufficient")
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "retrieved_contexts": [],
        "trace": ["Stopped → insufficient reliable evidence"],
    }


def create_agentic_rag_graph(
    classifier: WorkflowClassifier,
    *,
    model: BaseChatModel | None = None,
    kb_tool: BaseTool | None = None,
    web_tool: BaseTool | None = None,
) -> Any:
    """Build and compile the canonical 10-node StateGraph topology.

    Args:
        classifier: The WorkflowClassifier adapter driving routing and evidence grading.
        model: Optional BaseChatModel instance for query rewrite and answer generation.
            Defaults to get_chat_model().
        kb_tool: Optional BaseTool instance for knowledge base retrieval.
            Defaults to create_retriever_tool().
        web_tool: Optional BaseTool instance for web search fallback.
            Defaults to create_web_search_tool().

    Returns:
        Compiled StateGraph runnable wrapped with wrap_safe_invoke ready for execution.
    """
    active_model = model if model is not None else get_chat_model()
    active_kb_tool = kb_tool if kb_tool is not None else create_retriever_tool()
    active_web_tool = web_tool if web_tool is not None else create_web_search_tool()

    async def node_condense_query(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="condense_query")
        raw_history = state.get("messages", [])[:-1] if state.get("messages") else []
        history_str = _format_chat_history(raw_history)
        current_question = state["question"]
        prompt_obj = get_workflow_prompt("rag:condense_query")
        try:
            prompt = prompt_obj.compile(
                question=current_question,
                chat_history=history_str,
                history=history_str,
            )
        except TypeError:
            prompt = prompt_obj.compile(question=current_question)
        run_cfg = _build_prompt_config(prompt_obj, config)
        response = await active_model.ainvoke(prompt, config=run_cfg)
        raw_query = response.content if hasattr(response, "content") else str(response)
        standalone_query = (raw_query if isinstance(raw_query, str) else str(raw_query)).strip()
        return {
            "current_query": standalone_query,
            "trace": [f"Query contextualized → '{standalone_query}'"],
        }

    def node_route_question(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="route_question")
        messages = state.get("messages") or []
        query = state.get("current_query") if len(messages) > 1 else state["question"]
        if not query:
            query = state["question"]
        decision = classifier.route(query, config=config)
        return {
            "current_query": query,
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

    def node_grade_kb(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
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

    def node_grade_web(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
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

    async def rewrite_query(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="rewrite_query")
        prompt_obj = get_workflow_prompt("rag:rewrite_query")
        prompt = prompt_obj.compile(
            question=state["question"],
            current_query=state["current_query"],
        )
        run_cfg = _build_prompt_config(prompt_obj, config)
        response = await active_model.ainvoke(prompt, config=run_cfg)
        raw_rewritten = response.content if hasattr(response, "content") else str(response)
        rewritten = (raw_rewritten if isinstance(raw_rewritten, str) else str(raw_rewritten)).strip()
        return {
            "current_query": rewritten,
            "retry_count": state["retry_count"] + 1,
            "trace": [f"Query rewrite → {rewritten}"],
        }

    async def generate_from_kb(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="generate_from_kb")
        context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
        prompt_obj = get_workflow_prompt("rag:generate_kb")
        raw_history = state.get("messages", [])[:-1] if state.get("messages") else []
        if raw_history:
            history_str = _format_chat_history(raw_history)
            try:
                prompt = prompt_obj.compile(
                    question=state["question"],
                    context=context,
                    history=history_str,
                    chat_history=history_str,
                )
            except TypeError:
                prompt = prompt_obj.compile(
                    question=state["question"],
                    context=context,
                )
        else:
            prompt = prompt_obj.compile(
                question=state["question"],
                context=context,
            )
        run_cfg = _build_prompt_config(prompt_obj, config)
        response = await active_model.ainvoke(prompt, config=run_cfg)
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

    async def generate_from_web(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="generate_from_web")
        prompt_obj = get_workflow_prompt("rag:generate_web")
        raw_history = state.get("messages", [])[:-1] if state.get("messages") else []
        if raw_history:
            history_str = _format_chat_history(raw_history)
            try:
                prompt = prompt_obj.compile(
                    question=state["question"],
                    web_results=state["web_results"],
                    history=history_str,
                    chat_history=history_str,
                )
            except TypeError:
                prompt = prompt_obj.compile(
                    question=state["question"],
                    web_results=state["web_results"],
                )
        else:
            prompt = prompt_obj.compile(
                question=state["question"],
                web_results=state["web_results"],
            )
        run_cfg = _build_prompt_config(prompt_obj, config)
        response = await active_model.ainvoke(prompt, config=run_cfg)
        answer = response.content if hasattr(response, "content") else str(response)
        return {
            "answer": answer,
            "source_used": "web_search",
            "retrieved_contexts": state.get("retrieved_contexts", []),
            "citations": state.get("citations", []),
            "trace": ["Answer generation → WEB SEARCH"],
        }

    async def node_direct_answer(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        return await direct_answer(state, config=config, model=active_model)

    graph = StateGraph(AgentState)
    nodes = {
        "condense_query": node_condense_query,
        "route_question": node_route_question,
        "retrieve_kb": node_retrieve_kb,
        "grade_kb": node_grade_kb,
        "search_web": node_search_web,
        "grade_web": node_grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": node_direct_answer,
        "insufficient": insufficient,
    }
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_conditional_edges(
        START,
        route_start,
        {
            "condense_query": "condense_query",
            "route_question": "route_question",
        },
    )
    graph.add_edge("condense_query", "route_question")
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
