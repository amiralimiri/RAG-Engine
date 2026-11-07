import logging
from typing import Any, Literal, cast

import structlog
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.core.concurrency import wrap_safe_invoke
from app.core.config import get_settings
from app.rag.classifiers import WorkflowClassifier
from app.rag.prompts import PromptStore, RenderedPrompt, get_prompt_store
from app.rag.prompts.formatting import format_chat_history
from app.rag.state import AgentState
from app.rag.tools import RAGTool, create_retriever_tool, create_web_search_tool

logger = logging.getLogger(__name__)

GENERATION_NODES: set[str] = {"generate_from_kb", "generate_from_web", "direct_answer"}

__all__ = [
    "GENERATION_NODES",
    "get_chat_model",
    "create_agentic_rag_graph",
    "route_start",
]


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


def _prepare_generation_prompt(
    prompt_key: str,
    state: AgentState,
    config: RunnableConfig | None = None,
    prompt_store: PromptStore | None = None,
    **extra_kwargs: Any,
) -> RenderedPrompt:
    """Extract conversation history, format context variables, and compile prompt with history fallback prep."""
    store = prompt_store if prompt_store is not None else get_prompt_store()
    raw_history = state.get("messages", [])[:-1] if state.get("messages") else []
    history_str = format_chat_history(raw_history) if raw_history else ""
    current_q = state.get("current_query") or state["question"]

    compile_kwargs = {
        "question": state["question"],
        "current_query": current_q,
        **extra_kwargs,
    }

    if history_str:
        compile_kwargs["chat_history"] = history_str
        compile_kwargs["history"] = history_str

    rendered = store.render(prompt_key, config=config, **compile_kwargs)
    if history_str and history_str not in rendered.text:
        rendered = rendered.with_prefix(f"Chat History:\n{history_str}\n\n")

    return rendered


async def direct_answer(
    state: AgentState,
    config: RunnableConfig | None = None,
    *,
    model: BaseChatModel | None = None,
    prompt_store: PromptStore | None = None,
) -> dict[str, Any]:
    structlog.contextvars.bind_contextvars(step="direct_answer")
    rendered = _prepare_generation_prompt("rag:direct_answer", state, config=config, prompt_store=prompt_store)
    active_model = model if model is not None else get_chat_model()
    response = await active_model.ainvoke(rendered.text, config=cast(RunnableConfig, rendered.config))
    answer = response.content if hasattr(response, "content") else str(response)
    return {
        "answer": answer,
        "source_used": "direct",
        "retrieved_contexts": [],
        "citations": [],
        "trace": ["Direct response → no retrieval"],
    }


def insufficient(state: AgentState) -> dict[str, Any]:
    structlog.contextvars.bind_contextvars(step="insufficient")
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "retrieved_contexts": [],
        "citations": [],
        "trace": ["Stopped → insufficient reliable evidence"],
    }


def create_agentic_rag_graph(
    classifier: WorkflowClassifier,
    *,
    model: BaseChatModel | None = None,
    kb_tool: RAGTool | None = None,
    web_tool: RAGTool | None = None,
    prompt_store: PromptStore | None = None,
) -> Any:
    """Build and compile the canonical 11-node StateGraph topology.

    Args:
        classifier: The WorkflowClassifier adapter driving routing and evidence grading.
        model: Optional BaseChatModel instance for query rewrite and answer generation.
            Defaults to get_chat_model().
        kb_tool: Optional RAGTool instance for knowledge base retrieval.
            Defaults to create_retriever_tool().
        web_tool: Optional RAGTool instance for web search fallback.
            Defaults to create_web_search_tool().
        prompt_store: Optional PromptStore instance for prompt compilation and observation metadata.
            Defaults to get_prompt_store().

    Returns:
        Compiled StateGraph runnable wrapped with wrap_safe_invoke ready for execution.
    """
    active_model = model if model is not None else get_chat_model()
    active_kb_tool = kb_tool if kb_tool is not None else create_retriever_tool()
    active_web_tool = web_tool if web_tool is not None else create_web_search_tool()
    active_prompt_store = prompt_store if prompt_store is not None else get_prompt_store()

    async def node_condense_query(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="condense_query")
        raw_history = state.get("messages", [])[:-1] if state.get("messages") else []
        history_str = format_chat_history(raw_history)
        current_question = state["question"]
        rendered = active_prompt_store.render(
            "rag:condense_query",
            config=config,
            question=current_question,
            chat_history=history_str,
            history=history_str,
        )
        response = await active_model.ainvoke(rendered.text, config=cast(RunnableConfig, rendered.config))
        raw_query = response.content if hasattr(response, "content") else str(response)
        standalone_query = (raw_query if isinstance(raw_query, str) else str(raw_query)).strip()
        if not standalone_query:
            standalone_query = current_question
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
        search_query = state.get("current_query") or state["question"]
        result = await active_kb_tool.aexecute({"query": search_query}, config=config)

        docs = result.artifact.raw_payload if isinstance(result.artifact.raw_payload, list) else []
        citations = list(result.artifact.citations)
        retrieved_contexts = (
            result.artifact.retrieved_contexts
            if result.artifact.retrieved_contexts
            else ([result.content] if result.content else [])
        )

        return {
            "kb_docs": docs,
            "retrieved_contexts": retrieved_contexts,
            "citations": citations,
            "trace": [f"Private KB retrieval → {len(docs)} chunks"],
        }

    def node_grade_kb(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="grade_kb")
        search_query = state.get("current_query") or state["question"]
        grade = classifier.grade_kb(
            question=state["question"],
            current_query=search_query,
            kb_docs=state["kb_docs"],
            config=config,
        )
        return {
            "kb_grade": grade.grade,
            "trace": [f"KB evidence grade → {grade.grade.upper()}"],
        }

    async def node_search_web(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="search_web")
        search_query = state.get("current_query") or state["question"]
        result = await active_web_tool.aexecute({"query": search_query}, config=config)

        citations = list(result.artifact.citations)
        contexts = (
            result.artifact.retrieved_contexts
            if result.artifact.retrieved_contexts
            else ([result.content] if result.content else [])
        )

        return {
            "web_results": result.content,
            "citations": citations,
            "source_used": result.artifact.source_used,
            "retrieved_contexts": contexts,
            "trace": ["Web fallback → Tavily search"],
        }

    def node_grade_web(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="grade_web")
        search_query = state.get("current_query") or state["question"]
        grade = classifier.grade_web(
            question=state["question"],
            current_query=search_query,
            web_results=state["web_results"],
            config=config,
        )
        return {
            "web_grade": grade.grade,
            "trace": [f"Web evidence grade → {grade.grade.upper()}"],
        }

    async def rewrite_query(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="rewrite_query")
        search_query = state.get("current_query") or state["question"]
        rendered = active_prompt_store.render(
            "rag:rewrite_query",
            config=config,
            question=state["question"],
            current_query=search_query,
        )
        response = await active_model.ainvoke(rendered.text, config=cast(RunnableConfig, rendered.config))
        raw_rewritten = response.content if hasattr(response, "content") else str(response)
        rewritten = (raw_rewritten if isinstance(raw_rewritten, str) else str(raw_rewritten)).strip()
        return {
            "current_query": rewritten,
            "retry_count": state["retry_count"] + 1,
            "trace": [f"Query rewrite → {rewritten}"],
        }

    async def generate_from_kb(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="generate_from_kb")
        context = "\n\n".join(
            f"[Source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}" for d in state["kb_docs"]
        )
        rendered = _prepare_generation_prompt(
            "rag:generate_kb",
            state,
            config=config,
            prompt_store=active_prompt_store,
            context=context,
        )
        response = await active_model.ainvoke(rendered.text, config=cast(RunnableConfig, rendered.config))
        answer = response.content if hasattr(response, "content") else str(response)
        return {
            "answer": answer,
            "source_used": "vectorstore",
            "citations": state.get("citations", []),
            "retrieved_contexts": state.get("retrieved_contexts", []),
            "trace": ["Answer generation → PRIVATE KB"],
        }

    async def generate_from_web(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step="generate_from_web")
        rendered = _prepare_generation_prompt(
            "rag:generate_web",
            state,
            config=config,
            prompt_store=active_prompt_store,
            web_results=state["web_results"],
        )
        response = await active_model.ainvoke(rendered.text, config=cast(RunnableConfig, rendered.config))
        answer = response.content if hasattr(response, "content") else str(response)
        return {
            "answer": answer,
            "source_used": "web_search",
            "retrieved_contexts": state.get("retrieved_contexts", []),
            "citations": state.get("citations", []),
            "trace": ["Answer generation → WEB SEARCH"],
        }

    async def node_direct_answer(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        return await direct_answer(state, config=config, model=active_model, prompt_store=active_prompt_store)

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
    graph.add_conditional_edges(
        "route_question", route_after_router, {"retrieve_kb": "retrieve_kb", "direct_answer": "direct_answer"}
    )
    graph.add_edge("retrieve_kb", "grade_kb")
    graph.add_conditional_edges(
        "grade_kb", after_kb, {"generate_from_kb": "generate_from_kb", "search_web": "search_web"}
    )
    graph.add_edge("search_web", "grade_web")
    graph.add_conditional_edges(
        "grade_web",
        after_web,
        {
            "generate_from_web": "generate_from_web",
            "rewrite_query": "rewrite_query",
            "insufficient": "insufficient",
        },
    )
    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("insufficient", END)

    compiled = graph.compile()
    return wrap_safe_invoke(compiled)
