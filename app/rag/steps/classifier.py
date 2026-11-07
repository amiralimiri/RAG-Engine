"""Classifier workflow steps wrapping routing and evidence grading seam calls."""

from typing import Any, Literal

import structlog
from langchain_core.runnables import RunnableConfig

from app.rag.classifiers import WorkflowClassifier
from app.rag.steps.base import BaseStep, register_step

logger = structlog.get_logger(__name__)

GRADING_MODES = {"kb", "web"}
GradingMode = Literal["kb", "web"]


@register_step("classifier_route")
class ClassifierRouteStep(BaseStep):
    """Route a question through the WorkflowClassifier into ''kb'' or ''direct''."""

    def __init__(
        self,
        classifier: WorkflowClassifier,
        *,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            name=name or "route_question",
            inputs=inputs,
            outputs=outputs
            if outputs is not None
            else {
                "current_query": "current_query",
                "source_used": "source_used",
                "trace": "trace",
            },
        )
        self.classifier = classifier

    async def execute(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step=self.name)

        question = self.resolve_input(state, "question", default="") or ""
        messages = self.resolve_input(state, "messages", default=[]) or []
        current_query = self.resolve_input(state, "current_query")

        query = current_query if len(messages) > 1 else question
        if not query:
            query = question

        decision = self.classifier.route(query, config=config)
        return self.apply_outputs(
            {
                "current_query": query,
                "source_used": decision.route,
                "trace": [f"Router → {decision.route.upper()}"],
            },
            state,
        )


@register_step("classifier_grade")
class ClassifierGradingStep(BaseStep):
    """Grade retrieved KB or web evidence through the WorkflowClassifier seam."""

    def __init__(
        self,
        classifier: WorkflowClassifier,
        *,
        target: GradingMode | None = None,
        mode: GradingMode | None = None,
        name: str | None = None,
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        if target is not None and mode is not None and target != mode:
            raise ValueError(f"Conflicting grading target '{target}' and mode '{mode}'.")
        resolved_mode = target or mode or "kb"
        if resolved_mode not in GRADING_MODES:
            raise ValueError(f"Unsupported grading mode '{resolved_mode}'. Expected one of {sorted(GRADING_MODES)}.")

        self.mode = resolved_mode
        grade_key = f"{resolved_mode}_grade"
        super().__init__(
            name=name or f"grade_{resolved_mode}",
            inputs=inputs,
            outputs=outputs if outputs is not None else {grade_key: grade_key, "trace": "trace"},
        )
        self.classifier = classifier
        self.grade_key = grade_key

    async def execute(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
        structlog.contextvars.bind_contextvars(step=self.name)

        question = self.resolve_input(state, "question", default="") or ""
        search_query = self.resolve_input(state, "current_query") or question

        if self.mode == "kb":
            kb_docs = self.resolve_input(state, "kb_docs", default=[]) or []
            grade = self.classifier.grade_kb(
                question=question,
                current_query=search_query,
                kb_docs=list(kb_docs),
                config=config,
            )
            label = "KB"
        else:
            web_results = self.resolve_input(state, "web_results", default="") or ""
            grade = self.classifier.grade_web(
                question=question,
                current_query=search_query,
                web_results=web_results,
                config=config,
            )
            label = "Web"

        return self.apply_outputs(
            {
                self.grade_key: grade.grade,
                "trace": [f"{label} evidence grade → {grade.grade.upper()}"],
            },
            state,
        )


__all__ = ["ClassifierRouteStep", "ClassifierGradingStep", "GRADING_MODES", "GradingMode"]
