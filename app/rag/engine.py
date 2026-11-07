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
from app.rag.prompts import PromptStore, get_prompt_store
from app.rag.state import AgentState
from app.rag.steps import (
    ClassifierGradingStep,
    ClassifierRouteStep,
    PromptGenerationStep,
    QueryReformulationStep,
    ToolRetrievalStep,
)
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
    return "retrieve_kb" if state.get("source_used") == "kb" else "direct_answer"


def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state.get("kb_grade") == "good" else "search_web"


def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state.get("web_grade") == "good":
        return "generate_from_web"
    if state.get("retry_count", 0) < get_settings().max_retries:
        return "rewrite_query"
    return "insufficient"


async def direct_answer(
    state: AgentState,
    config: RunnableConfig | None = None,
    *,
    model: BaseChatModel | None = None,
    prompt_store: PromptStore | None = None,
) -> dict[str, Any]:
    """Execute the direct-response path through the canonical generation step."""
    active_model = model if model is not None else get_chat_model()
    step = PromptGenerationStep(
        active_model,
        mode="direct",
        prompt_key="rag:direct_answer",
        source_used="direct",
        prompt_store=prompt_store,
    )
    return await step(cast(dict[str, Any], state), config)


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

    condense_query = QueryReformulationStep(active_model, mode="condense", prompt_store=active_prompt_store)
    route_question = ClassifierRouteStep(classifier)
    retrieve_kb = ToolRetrievalStep(
        active_kb_tool,
        name="retrieve_kb",
        trace_template="Private KB retrieval → {count} chunks",
        source_used="vectorstore",
        emit_source_used=False,
        outputs={
            "documents": "kb_docs",
            "retrieved_contexts": "retrieved_contexts",
            "citations": "citations",
            "trace": "trace",
        },
    )
    grade_kb = ClassifierGradingStep(classifier, target="kb")
    search_web = ToolRetrievalStep(
        active_web_tool,
        name="search_web",
        trace_template="Web fallback → Tavily search",
        source_used=None,
        emit_source_used=True,
        outputs={
            "content": "web_results",
            "retrieved_contexts": "retrieved_contexts",
            "citations": "citations",
            "source_used": "source_used",
            "trace": "trace",
        },
    )
    grade_web = ClassifierGradingStep(classifier, target="web")
    rewrite_query = QueryReformulationStep(active_model, mode="rewrite", prompt_store=active_prompt_store)
    generate_from_kb = PromptGenerationStep(
        active_model,
        mode="kb",
        prompt_key="rag:generate_kb",
        source_used="vectorstore",
        prompt_store=active_prompt_store,
    )
    generate_from_web = PromptGenerationStep(
        active_model,
        mode="web",
        prompt_key="rag:generate_web",
        source_used="web_search",
        prompt_store=active_prompt_store,
    )
    direct_answer_step = PromptGenerationStep(
        active_model,
        mode="direct",
        prompt_key="rag:direct_answer",
        source_used="direct",
        prompt_store=active_prompt_store,
    )

    graph = StateGraph(AgentState)
    nodes: list[tuple[str, Any]] = [
        ("condense_query", condense_query),
        ("route_question", route_question),
        ("retrieve_kb", retrieve_kb),
        ("grade_kb", grade_kb),
        ("search_web", search_web),
        ("grade_web", grade_web),
        ("rewrite_query", rewrite_query),
        ("generate_from_kb", generate_from_kb),
        ("generate_from_web", generate_from_web),
        ("direct_answer", direct_answer_step),
        ("insufficient", insufficient),
    ]
    for node_name, step in nodes:
        graph.add_node(node_name, step)

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
