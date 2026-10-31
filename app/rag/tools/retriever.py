"""Knowledge base retrieval tool for LangChain and LangGraph agents."""

import inspect
from typing import Any

import structlog
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.rag.tools.base import _UNSET, DualOutputStructuredTool, get_invoke_config

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


def _extract_citations(docs: list[Document]) -> list[dict[str, Any]]:
    citations = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown") if hasattr(doc, "metadata") and doc.metadata else "unknown"
        title = source.split("/")[-1].split("\\")[-1]
        if not any(c.get("title") == title for c in citations):
            citations.append({"title": title, "url": "", "type": "private_kb"})
    return citations


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
            if callable(retriever) and not hasattr(retriever, "invoke") and not hasattr(retriever, "ainvoke"):
                return retriever()
            return retriever
        from app.rag.knowledge import get_knowledge_store
        return get_knowledge_store().as_retriever()

    def _run(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]:
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        active = _get_active_retriever()
        effective_config = config
        orig_config = get_invoke_config()
        if orig_config is not _UNSET:
            effective_config = orig_config
        docs = active.invoke(query, config=effective_config)
        docs = docs or []
        citations = _extract_citations(docs)
        artifact = {
            "documents": docs,
            "citations": citations,
        }
        return _format_docs(docs), artifact

    async def _arun(query: str, config: RunnableConfig | None = None) -> tuple[str, dict[str, Any]]:
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        active = _get_active_retriever()
        effective_config = config
        orig_config = get_invoke_config()
        if orig_config is not _UNSET:
            effective_config = orig_config
        if hasattr(active, "ainvoke"):
            maybe_coro = active.ainvoke(query, config=effective_config)
            if inspect.isawaitable(maybe_coro):
                docs = await maybe_coro
            else:
                docs = active.invoke(query, config=effective_config)
        else:
            docs = active.invoke(query, config=effective_config)
        docs = docs or []
        citations = _extract_citations(docs)
        artifact = {
            "documents": docs,
            "citations": citations,
        }
        return _format_docs(docs), artifact

    return DualOutputStructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=name,
        description=description,
        args_schema=KnowledgeSearchInput,
        response_format="content_and_artifact",
    )


__all__ = [
    "KnowledgeSearchInput",
    "create_retriever_tool",
]
