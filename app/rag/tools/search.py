"""Web search tool wrapping Tavily for LangChain and LangGraph agents."""

import inspect
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.rag.tools.base import _UNSET, DualOutputStructuredTool, get_invoke_config

logger = structlog.get_logger(__name__)


class WebSearchInput(BaseModel):
    """Input payload for live external web search."""

    query: str = Field(description="Search query for live external internet search")


def _format_search_results(result: Any) -> str:
    lines = []
    if isinstance(result, dict):
        if result.get("answer"):
            lines.append("Search answer: " + result["answer"])
        for item in result.get("results", []):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"Title: {title}\nURL: {url}\nContent: {content}")
    elif isinstance(result, str):
        lines.append(result)
    else:
        lines.append(str(result))

    return "\n\n".join(lines) if lines else "No relevant web search results found."


def _extract_web_citations(result: Any) -> list[dict[str, Any]]:
    citations = []
    if isinstance(result, dict):
        for item in result.get("results", []):
            title = item.get("title", "")
            url = item.get("url", "")
            if not any(c.get("url") == url for c in citations):
                citations.append({"title": title or url, "url": url, "type": "web"})
    return citations


def _extract_web_contexts(result: Any) -> list[str]:
    contexts = []
    if isinstance(result, dict):
        for item in result.get("results", []):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            contexts.append(f"Title: {title}\nURL: {url}\nContent: {content}")
    elif isinstance(result, str):
        if result:
            contexts.append(result)
    elif result is not None:
        contexts.append(str(result))
    return contexts


def create_web_search_tool(
    search_client: Any = None,
    name: str = "web_search",
    description: str = (
        "Search the public web for external technical documentation, official software manuals, "
        "and internet troubleshooting resources when internal documentation is absent or insufficient."
    ),
) -> BaseTool:
    """Create a structured BaseTool wrapping Tavily internet search."""

    def _get_active_client():
        if search_client is not None:
            if callable(search_client) and not hasattr(search_client, "invoke") and not hasattr(search_client, "ainvoke"):
                return search_client()
            return search_client
        from langchain_tavily import TavilySearch
        settings = get_settings()
        api_key = settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else ""
        return TavilySearch(
            tavily_api_key=api_key,
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )

    def _run(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]:
        structlog.contextvars.bind_contextvars(step="search_web")
        client = _get_active_client()
        effective_config = config
        orig_config = get_invoke_config()
        if orig_config is not _UNSET:
            effective_config = orig_config

        result = client.invoke({"query": query}, config=effective_config)
        artifact = {
            "citations": _extract_web_citations(result),
            "retrieved_contexts": _extract_web_contexts(result),
        }
        return _format_search_results(result), artifact

    async def _arun(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]:
        structlog.contextvars.bind_contextvars(step="search_web")
        client = _get_active_client()
        effective_config = config
        orig_config = get_invoke_config()
        if orig_config is not _UNSET:
            effective_config = orig_config

        if hasattr(client, "ainvoke"):
            maybe_coro = client.ainvoke({"query": query}, config=effective_config)
            if inspect.isawaitable(maybe_coro):
                result = await maybe_coro
            else:
                result = client.invoke({"query": query}, config=effective_config)
        else:
            result = client.invoke({"query": query}, config=effective_config)
        artifact = {
            "citations": _extract_web_citations(result),
            "retrieved_contexts": _extract_web_contexts(result),
        }
        return _format_search_results(result), artifact

    return DualOutputStructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=name,
        description=description,
        args_schema=WebSearchInput,
        response_format="content_and_artifact",
    )


__all__ = [
    "WebSearchInput",
    "create_web_search_tool",
]
