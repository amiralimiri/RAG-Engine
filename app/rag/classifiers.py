import logging
from typing import Any, Protocol, cast, runtime_checkable

import structlog
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_typesafe import Choice, TypeSafeClassifier

from app.core.config import get_settings
from app.core.telemetry import extract_trace_id, get_langfuse_client
from app.rag.prompts import PromptStore, RenderedPrompt, get_prompt_store
from app.rag.state import EvidenceGrade, RouteDecision

logger = logging.getLogger(__name__)

__all__ = [
    "WorkflowClassifier",
    "FakeClassifierAdapter",
    "OpenAIClassifierAdapter",
    "JevClassifierAdapter",
    "_estimate_jev_tokens",
    "_record_jev_generation",
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


def _estimate_jev_tokens(prompt_text: str, output_text: str) -> dict[str, int]:
    """Heuristic rule-of-thumb: 1 token ~= 4 characters."""
    input_tokens = max(1, len(prompt_text) // 4)
    output_tokens = max(1, len(output_text) // 4)
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total": input_tokens + output_tokens,
    }


def _record_jev_generation(
    name: str,
    input_text: str,
    output_text: str,
    trace_id: str | None = None,
    rendered: RenderedPrompt | None = None,
    prompt_client: Any | None = None,
    config: RunnableConfig | dict[str, Any] | None = None,
) -> None:
    try:
        client = get_langfuse_client()
        if not client:
            return
        current_settings = get_settings()

        active_config = rendered.config if rendered is not None else config
        active_trace_id = trace_id
        if not active_trace_id and active_config and isinstance(active_config, dict):
            callbacks = active_config.get("callbacks") or []
            if isinstance(callbacks, list):
                for cb in callbacks:
                    active_trace_id = extract_trace_id(cb)
                    if active_trace_id:
                        break
            if not active_trace_id:
                meta = active_config.get("metadata")
                if isinstance(meta, dict):
                    active_trace_id = meta.get("trace_id")

        if not active_trace_id:
            active_trace_id = structlog.contextvars.get_contextvars().get("trace_id")

        trace_context = {"trace_id": str(active_trace_id)} if active_trace_id else None
        usage = _estimate_jev_tokens(input_text, output_text)

        input_cost = (usage["input"] * current_settings.jev_cost_per_1k_input_tokens) / 1000.0
        output_cost = (usage["output"] * current_settings.jev_cost_per_1k_output_tokens) / 1000.0
        cost_details = {
            "input": input_cost,
            "output": output_cost,
            "total": input_cost + output_cost,
        }

        meta_dict: dict[str, Any] = {"classifier": "typesafe_jev"}
        if active_config and isinstance(active_config, dict):
            cfg_meta = active_config.get("metadata")
            if isinstance(cfg_meta, dict):
                meta_dict.update(cfg_meta)

        prompt_target = (
            rendered.raw_prompt
            if (rendered is not None and rendered.raw_prompt is not None and not getattr(rendered.raw_prompt, "is_fallback", False))
            else (prompt_client if (prompt_client is not None and not getattr(prompt_client, "is_fallback", False)) else None)
        )
        if prompt_target is None and prompt_client is not None:
            prompt_name = getattr(prompt_client, "name", None)
            if prompt_name:
                meta_dict["prompt_name"] = prompt_name
            if getattr(prompt_client, "is_fallback", False):
                meta_dict["prompt_fallback"] = True

        if hasattr(client, "start_observation"):
            obs_kwargs: dict[str, Any] = {
                "name": name,
                "as_type": "generation",
                "model": current_settings.jev_model,
                "input": input_text,
                "output": output_text,
                "usage_details": usage,
                "cost_details": cost_details,
                "metadata": meta_dict,
            }
            if prompt_target is not None:
                obs_kwargs["prompt"] = prompt_target
            if trace_context:
                obs_kwargs["trace_context"] = trace_context
            obs = client.start_observation(**obs_kwargs)
            if hasattr(obs, "update"):
                obs.update(
                    usage_details=usage,
                    cost_details=cost_details,
                    output=output_text,
                )
            if hasattr(obs, "end"):
                obs.end()
        elif hasattr(client, "generation"):
            gen_kwargs: dict[str, Any] = {
                "name": name,
                "model": current_settings.jev_model,
                "input": input_text,
                "output": output_text,
                "usage": usage,
                "metadata": meta_dict,
            }
            if prompt_target is not None:
                gen_kwargs["prompt"] = prompt_target
            if active_trace_id:
                gen_kwargs["trace_id"] = str(active_trace_id)
            getattr(client, "generation")(**gen_kwargs)
    except Exception as exc:
        logger.debug("Failed to record JEV generation span: %s", exc)


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
            res = self.route_decision.pop(0) if len(self.route_decision) > 1 else self.route_decision[0]
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
            res = self.kb_grade.pop(0) if len(self.kb_grade) > 1 else self.kb_grade[0]
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
            res = self.web_grade.pop(0) if len(self.web_grade) > 1 else self.web_grade[0]
        else:
            res = self.web_grade
        return _normalize_grade(res)


def _fallback_evidence_grade(exc: Exception) -> EvidenceGrade:
    """Safe fallback evidence grade when structured grader invocation fails."""
    logger.warning("evidence_grade_structured_parsing_failed: %s", exc)
    return EvidenceGrade(grade="weak")


class OpenAIClassifierAdapter:
    """Production classifier adapter backed by ChatOpenAI structured outputs."""

    def __init__(
        self,
        chat_model: Any = None,
        prompt_store: PromptStore | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._prompt_store = prompt_store

    @property
    def prompt_store(self) -> PromptStore:
        if self._prompt_store is None:
            self._prompt_store = get_prompt_store()
        return self._prompt_store

    def _get_chat_model(self) -> Any:
        if self._chat_model is None:
            settings = get_settings()
            self._chat_model = ChatOpenAI(
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                temperature=0,
                stream_usage=True,
            )
        return self._chat_model

    def route(
        self,
        question: str,
        config: RunnableConfig | None = None,
    ) -> RouteDecision:
        router = self._get_chat_model().with_structured_output(RouteDecision, method="json_mode")
        rendered = self.prompt_store.render("rag:router:llm", config=config, question=question)
        decision = cast(RouteDecision, router.invoke(rendered.text, config=cast(RunnableConfig, rendered.config)))
        if isinstance(decision, dict):
            decision = RouteDecision(**decision)
        return decision

    def grade_kb(
        self,
        question: str,
        current_query: str,
        kb_docs: list[Document],
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        grader = self._get_chat_model().with_structured_output(EvidenceGrade, method="json_mode")
        context = "\n\n".join(f"Source: {d.metadata.get('source', 'unknown')}\n{d.page_content}" for d in kb_docs)
        rendered = self.prompt_store.render(
            "rag:grade_kb:llm",
            config=config,
            question=question,
            current_query=current_query,
            context=context,
        )
        try:
            grade = cast(EvidenceGrade, grader.invoke(rendered.text, config=cast(RunnableConfig, rendered.config)))
            if isinstance(grade, dict):
                grade = EvidenceGrade(**grade)
            return grade
        except Exception as e:
            return _fallback_evidence_grade(e)

    def grade_web(
        self,
        question: str,
        current_query: str,
        web_results: str,
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        grader = self._get_chat_model().with_structured_output(EvidenceGrade, method="json_mode")
        rendered = self.prompt_store.render(
            "rag:grade_web:llm",
            config=config,
            question=question,
            current_query=current_query,
            web_results=web_results,
        )
        try:
            grade = cast(EvidenceGrade, grader.invoke(rendered.text, config=cast(RunnableConfig, rendered.config)))
            if isinstance(grade, dict):
                grade = EvidenceGrade(**grade)
            return grade
        except Exception as e:
            return _fallback_evidence_grade(e)


class JevClassifierAdapter:
    """Production classifier adapter backed by TypeSafeClassifier."""

    def __init__(
        self,
        classifier: Any = None,
        prompt_store: PromptStore | None = None,
    ) -> None:
        self._classifier = classifier
        self._prompt_store = prompt_store

    @property
    def prompt_store(self) -> PromptStore:
        if self._prompt_store is None:
            self._prompt_store = get_prompt_store()
        return self._prompt_store

    def _get_classifier(self) -> Any:
        if self._classifier is None:
            settings = get_settings()
            self._classifier = TypeSafeClassifier(
                api_key=settings.jev_api_key,
                base_url=settings.jev_base_url,
                model=settings.jev_model,
            )
        return self._classifier

    def route(
        self,
        question: str,
        config: RunnableConfig | None = None,
    ) -> RouteDecision:
        rendered = self.prompt_store.render("rag:router:jev", config=config, question=question)
        result = self._get_classifier().invoke(
            {
                "state": question,
                "questions": {
                    "route": Choice(
                        instructions="Which path should this enterprise IT support question take?",
                        criteria={
                            "kb": (
                                "Questions about company IT policies, VPN, password reset, "
                                "MFA, laptop setup, software access, security, email, "
                                "devices, troubleshooting, or any technology support that "
                                "requires company knowledge."
                            ),
                            "direct": ("Only greetings, thanks, or casual chat that needs no company knowledge."),
                        },
                    )
                },
            }
        )
        decision = result.choices["route"].choice
        _record_jev_generation(
            "jev_router",
            question,
            decision,
            rendered=rendered,
        )
        return RouteDecision(route=decision)

    def grade_kb(
        self,
        question: str,
        current_query: str,
        kb_docs: list[Document],
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        context = "\n\n".join(f"Source: {d.metadata.get('source', 'unknown')}\n{d.page_content}" for d in kb_docs)
        rendered = self.prompt_store.render(
            "rag:grade_kb:jev",
            config=config,
            question=question,
            current_query=current_query,
            context=context,
        )
        prompt_state = (
            f"Original user question:\n{question}\n\n"
            f"Query used for retrieval (search aid only):\n{current_query}\n\n"
            f"Private company KB evidence:\n{context}"
        )
        result = self._get_classifier().invoke(
            {
                "state": prompt_state,
                "questions": {
                    "grade": Choice(
                        instructions=(
                            "Is the provided private company KB evidence sufficient to "
                            "answer the ORIGINAL user question confidently and "
                            "specifically? Judge against the original question, not the "
                            "retrieval query — the retrieval query is only a search aid."
                        ),
                        criteria={
                            "good": (
                                "The evidence supports a sufficiently specific, "
                                "confident answer to the ORIGINAL user question."
                            ),
                            "weak": (
                                "The evidence is missing, incomplete, tangential, or only "
                                "relevant to the retrieval query — not enough to answer "
                                "the ORIGINAL user question specifically and confidently."
                            ),
                        },
                    )
                },
            }
        )
        grade = result.choices["grade"].choice
        _record_jev_generation(
            "jev_kb_grader",
            prompt_state,
            grade,
            rendered=rendered,
        )
        return EvidenceGrade(grade=grade)

    def grade_web(
        self,
        question: str,
        current_query: str,
        web_results: str,
        config: RunnableConfig | None = None,
    ) -> EvidenceGrade:
        rendered = self.prompt_store.render(
            "rag:grade_web:jev",
            config=config,
            question=question,
            current_query=current_query,
            web_results=web_results,
        )
        prompt_state = (
            f"Original user question:\n{question}\n\n"
            f"Query used for web search (retrieval aid only):\n{current_query}\n\n"
            f"Web evidence:\n{web_results}"
        )
        result = self._get_classifier().invoke(
            {
                "state": prompt_state,
                "questions": {
                    "grade": Choice(
                        instructions=(
                            "Is the provided web evidence sufficient, relevant, and "
                            "specific enough to answer the ORIGINAL user question "
                            "confidently? Judge against the original question, not the "
                            "search query — the search query is only a retrieval aid."
                        ),
                        criteria={
                            "good": (
                                "The web evidence directly supports a sufficiently "
                                "specific, confident answer to the ORIGINAL user "
                                "question."
                            ),
                            "weak": (
                                "The web evidence is missing, incomplete, tangential, or "
                                "only matches the search query — not enough to answer "
                                "the ORIGINAL user question specifically and confidently."
                            ),
                        },
                    )
                },
            }
        )
        grade = result.choices["grade"].choice
        _record_jev_generation(
            "jev_web_grader",
            prompt_state,
            grade,
            rendered=rendered,
        )
        return EvidenceGrade(grade=grade)
