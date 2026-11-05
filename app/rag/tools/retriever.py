"""Knowledge base retrieval tool for LangChain and LangGraph agents."""

import inspect
from typing import Any

import structlog
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.rag.tools.base import Citation, RAGTool, ToolEvidenceArtifact, rag_tool

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


def _extract_citations(docs: list[Document]) -> list[Citation]:
    citations: list[Citation] = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown") if hasattr(doc, "metadata") and doc.metadata else "unknown"
        title = source.split("/")[-1].split("\\")[-1]
        if not any(c.title == title for c in citations):
            citations.append(Citation(title=title, url="", type="private_kb"))
    return citations


def _extract_contexts(docs: list[Document]) -> list[str]:
    return [doc.page_content if hasattr(doc, "page_content") else str(doc) for doc in docs]


def create_retriever_tool(
    retriever: Any = None,
    name: str = "knowledge_base_search",
    description: str = (
        "Search the internal company knowledge base and IT documentation for technical policies, "
        "VPN, hardware, software installation, and account setup guidelines."
    ),
) -> RAGTool:
    """Create a structured RAGTool wrapping vectorstore retrieval."""

    def _get_active_retriever() -> Any:
        if retriever is not None:
            if callable(retriever) and not hasattr(retriever, "invoke") and not hasattr(retriever, "ainvoke"):
                return retriever()
            return retriever
        from app.rag.knowledge import get_knowledge_store

        return get_knowledge_store().as_retriever()

    def _run(query: str, config: RunnableConfig | None = None) -> tuple[str, ToolEvidenceArtifact]:
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        active: Any = _get_active_retriever()
        docs = active.invoke(query, config=config)
        docs = docs or []
        citations = _extract_citations(docs)
        contexts = _extract_contexts(docs)
        artifact = ToolEvidenceArtifact(
            citations=citations,
            retrieved_contexts=contexts,
            source_used="vectorstore",
            raw_payload=docs,
        )
        return _format_docs(docs), artifact

    async def _arun(query: str, config: RunnableConfig | None = None) -> tuple[str, ToolEvidenceArtifact]:
        structlog.contextvars.bind_contextvars(step="retrieve_kb")
        active: Any = _get_active_retriever()
        if hasattr(active, "ainvoke"):
            maybe_coro = active.ainvoke(query, config=config)
            if inspect.isawaitable(maybe_coro):
                docs = await maybe_coro
            else:
                docs = active.invoke(query, config=config)
        else:
            docs = active.invoke(query, config=config)
        docs = docs or []
        citations = _extract_citations(docs)
        contexts = _extract_contexts(docs)
        artifact = ToolEvidenceArtifact(
            citations=citations,
            retrieved_contexts=contexts,
            source_used="vectorstore",
            raw_payload=docs,
        )
        return _format_docs(docs), artifact

    return rag_tool(
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
