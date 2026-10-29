"""Web search tool wrapping Tavily for LangChain and LangGraph agents."""

from typing import Any
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
import structlog

from app.core.config import get_settings

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

    def _run(query: str, config: RunnableConfig | None = None) -> str:
        structlog.contextvars.bind_contextvars(step="search_web")
        client = _get_active_client()
        result = client.invoke({"query": query}, config=config)
        return _format_search_results(result)

    async def _arun(query: str, config: RunnableConfig | None = None) -> str:
        structlog.contextvars.bind_contextvars(step="search_web")
        client = _get_active_client()
        if hasattr(client, "ainvoke"):
            result = await client.ainvoke({"query": query}, config=config)
        else:
            result = client.invoke({"query": query}, config=config)
        return _format_search_results(result)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=name,
        description=description,
        args_schema=WebSearchInput,
    )


__all__ = [
    "WebSearchInput",
    "create_web_search_tool",
]
