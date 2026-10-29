from typing import Any, Protocol, runtime_checkable
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig

from app.rag.state import EvidenceGrade, RouteDecision

__all__ = [
    "WorkflowClassifier",
    "FakeClassifierAdapter",
]


@runtime_checkable
class WorkflowClassifier(Protocol):
    """Protocol seam for routing and grading classifiers in agentic RAG workflows."""

    def route(
        self,
        question: str,
        config: RunnableConfig | None = None,
    ) -> RouteDecision:
        """Classify user intent to route either to private knowledge base or direct answer."""
        ...

    def grade_kb(
        self,
        question: str,
        current_query: str,
        kb_docs: list[Document],
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        """Evaluate if retrieved knowledge base documents are sufficient to answer."""
        ...

    def grade_web(
        self,
        question: str,
        current_query: str,
        web_results: str,
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        """Evaluate if web search results are sufficient to answer."""
        ...


def _normalize_route(val: RouteDecision | str) -> RouteDecision:
    if isinstance(val, RouteDecision):
        return val
    return RouteDecision(route=val)  # type: ignore[arg-type]


def _normalize_grade(val: EvidenceGrade | str) -> EvidenceGrade:
    if isinstance(val, EvidenceGrade):
        return val
    return EvidenceGrade(grade=val)  # type: ignore[arg-type]


class FakeClassifierAdapter:
    """In-memory test double for WorkflowClassifier with configurable return values."""

    def __init__(
        self,
        route_decision: RouteDecision | str | list[RouteDecision | str] = "kb",
        kb_grade: EvidenceGrade | str | list[EvidenceGrade | str] = "good",
        web_grade: EvidenceGrade | str | list[EvidenceGrade | str] = "good",
    ) -> None:
        self.route_decision = route_decision
        self.kb_grade = kb_grade
        self.web_grade = web_grade
        self.route_calls: list[dict[str, Any]] = []
        self.grade_kb_calls: list[dict[str, Any]] = []
        self.grade_web_calls: list[dict[str, Any]] = []

    def route(
        self,
        question: str,
        config: RunnableConfig | None = None,
    ) -> RouteDecision:
        self.route_calls.append({"question": question, "config": config})
        if isinstance(self.route_decision, list):
            res = (
                self.route_decision.pop(0)
                if len(self.route_decision) > 1
                else self.route_decision[0]
            )
        else:
            res = self.route_decision
        return _normalize_route(res)

    def grade_kb(
        self,
        question: str,
        current_query: str,
        kb_docs: list[Document],
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        self.grade_kb_calls.append(
            {
                "question": question,
                "current_query": current_query,
                "kb_docs": kb_docs,
                "config": config,
            }
        )
        if isinstance(self.kb_grade, list):
            res = (
                self.kb_grade.pop(0)
                if len(self.kb_grade) > 1
                else self.kb_grade[0]
            )
        else:
            res = self.kb_grade
        return _normalize_grade(res)

    def grade_web(
        self,
        question: str,
        current_query: str,
        web_results: str,
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        self.grade_web_calls.append(
            {
                "question": question,
                "current_query": current_query,
                "web_results": web_results,
                "config": config,
            }
        )
        if isinstance(self.web_grade, list):
            res = (
                self.web_grade.pop(0)
                if len(self.web_grade) > 1
                else self.web_grade[0]
            )
        else:
            res = self.web_grade
        return _normalize_grade(res)
