"""StateGraph implementation for Sample Studio Research Agent."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.rag.workflows.base import StandardWorkflowState

try:
    from .prompts import CONCISE_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT, get_prompt_template
except (ImportError, ValueError):
    from prompts import (  # type: ignore[no-redef]
        CONCISE_SYSTEM_PROMPT,
        DEFAULT_SYSTEM_PROMPT,
        get_prompt_template,
    )

__all__ = [
    "CONCISE_SYSTEM_PROMPT",
    "DEFAULT_SYSTEM_PROMPT",
    "analyze_intent",
    "generate_response",
    "get_graph",
    "mock_search",
]


def analyze_intent(state: StandardWorkflowState) -> dict[str, Any]:
    """Classify user query intent and record rewritten query in state."""
    question = (state.get("question") or "").strip()
    trace = list(state.get("trace") or [])
    trace.append(f"Intent classified for query: '{question}'")
    return {
        "rewritten_query": question,
        "trace": trace,
    }


def mock_search(state: StandardWorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """Retrieve simulated studio context passages and domain citations."""
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    top_k = configurable.get("top_k", 2)
    question = state.get("question", "")

    contexts = [
        f"Context chunk #{i + 1}: Verified evidence supporting research on '{question}'."
        for i in range(top_k)
    ]
    citations = [
        {"title": f"Studio KB Document {i + 1}", "url": f"https://studio.internal/docs/{i + 1}"}
        for i in range(top_k)
    ]

    trace = list(state.get("trace") or [])
    trace.append(f"Retrieved {len(contexts)} contexts from knowledge base")

    return {
        "retrieved_contexts": contexts,
        "citations": citations,
        "source_used": "knowledge_base",
        "trace": trace,
    }


def generate_response(state: StandardWorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """Synthesize response using configured model and prompt variant."""
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    model_name = configurable.get("model_name", "mock-studio-model")
    prompt_version = configurable.get("prompt_version", "default")

    system_prompt = get_prompt_template(prompt_version)
    contexts = state.get("retrieved_contexts") or []
    question = state.get("question") or ""

    answer_text = (
        f"[{model_name}:{prompt_version}] Based on {len(contexts)} source(s): "
        f"Synthesized response for '{question}'."
    )

    # Invoke fake streaming chat model to trigger LangGraph on_chat_model_stream events
    llm = FakeListChatModel(responses=[answer_text])
    _ = llm.invoke(f"{system_prompt}\n\nQuestion: {question}")

    messages = list(state.get("messages") or [])
    messages.append(AIMessage(content=answer_text))

    trace = list(state.get("trace") or [])
    trace.append("Response synthesized successfully")

    return {
        "answer": answer_text,
        "messages": messages,
        "trace": trace,
    }


def get_graph(config: dict[str, Any] | None = None) -> Any:
    """Compile and return runnable StateGraph parameterized by config."""
    factory_configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    factory_model = factory_configurable.get("model_name")
    factory_prompt = factory_configurable.get("prompt_version")

    def _generate_node(state: StandardWorkflowState, config: RunnableConfig) -> dict[str, Any]:
        cfg = dict(config.get("configurable", {})) if isinstance(config, dict) else {}
        if factory_model and "model_name" not in cfg:
            cfg["model_name"] = factory_model
        if factory_prompt and "prompt_version" not in cfg:
            cfg["prompt_version"] = factory_prompt

        effective_config = dict(config) if isinstance(config, dict) else {}
        effective_config["configurable"] = cfg
        return generate_response(state, effective_config)

    builder = StateGraph(StandardWorkflowState)
    builder.add_node("analyze_intent", analyze_intent)
    builder.add_node("mock_search", mock_search)
    builder.add_node("generate_response", _generate_node)

    builder.add_edge(START, "analyze_intent")
    builder.add_edge("analyze_intent", "mock_search")
    builder.add_edge("mock_search", "generate_response")
    builder.add_edge("generate_response", END)

    return builder.compile()
