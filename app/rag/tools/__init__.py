"""Modular LangChain BaseTool implementations for RAG workflows and autonomous agents."""

from app.rag.tools.base import (
    AggregatedEvidence,
    FakeRAGTool,
    RAGTool,
    ToolEvidenceArtifact,
    ToolResult,
    aggregate_tool_evidence,
    rag_tool,
)
from app.rag.tools.retriever import KnowledgeSearchInput, create_retriever_tool
from app.rag.tools.search import WebSearchInput, create_web_search_tool

__all__ = [
    "RAGTool",
    "rag_tool",
    "FakeRAGTool",
    "ToolEvidenceArtifact",
    "ToolResult",
    "AggregatedEvidence",
    "aggregate_tool_evidence",
    "create_retriever_tool",
    "create_web_search_tool",
    "KnowledgeSearchInput",
    "WebSearchInput",
]
