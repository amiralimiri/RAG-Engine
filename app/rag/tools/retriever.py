"""Knowledge base retrieval tool for LangChain and LangGraph agents."""

from typing import Any
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
import structlog

logger = structlog.get_logger(__name__)


class KnowledgeSearchInput(BaseModel):
    """Input payload for searching internal knowledge base."""

    query: str = Field(description="Search query to find internal documentation")


def _format_docs(docs: list[Document]) -> str:
    if not docs:
        return "No relevant internal documentation found."
    formatted = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown") if hasattr(doc, "metadata") and doc.metadata else "unknown"
        content = doc.page_content if hasattr(doc, "page_content") else str(doc)
        formatted.append(f"[Source: {source}]\n{content}")
    return "\n\n".join(formatted)


def create_retriever_tool(
    retriever: Any = None,
    name: str = "knowledge_base_search",
    description: str = (
        "Search the internal company knowledge base and IT documentation for technical policies, "
        "VPN, hardware, software installation, and account setup guidelines."
    ),
) -> BaseTool:
    """Create a structured BaseTool wrapping vectorstore retrieval."""

    def _get_active_retriever():
        if retriever is not None:
            return retriever
        from app.rag.vectorstore import get_retriever
        return get_retriever()

    def _run(query: str, config: RunnableConfig | None = None) -> str:
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        active = _get_active_retriever()
        docs = active.invoke(query, config=config)
        return _format_docs(docs)

    async def _arun(query: str, config: RunnableConfig | None = None) -> str:
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        active = _get_active_retriever()
        if hasattr(active, "ainvoke"):
            docs = await active.ainvoke(query, config=config)
        else:
            docs = active.invoke(query, config=config)
        return _format_docs(docs)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=name,
        description=description,
        args_schema=KnowledgeSearchInput,
    )


__all__ = [
    "KnowledgeSearchInput",
    "create_retriever_tool",
]
