"""Legacy compatibility facade for monkeypatched workflow dictionaries and ask shims."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Callable

from app.rag.workflows.base import EvalContext, WorkflowDefinition, WorkflowInfo
from app.rag.workflows.registry import get_workflow_registry
from app.rag.workflows.runner import DEFAULT_STEP_LABELS
from app.rag.workflows.self_corrective import DEFAULT_GENERATION_NODES

if TYPE_CHECKING:
    from app.api.schemas import ChatRequest, ChatResponse


class _LegacyUnifiedWorkflowAdapter:
    """Adapts monkeypatched legacy ask functions and graphs into WorkflowDefinition."""

    def __init__(
        self,
        workflow_id: str,
        ask_fn: Callable[..., Any] | None = None,
        graph: Any = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.ask_fn = ask_fn
        self.graph = graph
        classifier_meta = "openai_json" if workflow_id == "fix_single_llm" else "typesafe_jev"
        self.info = WorkflowInfo(
            id=workflow_id,
            name=f"Legacy Adapted {workflow_id}",
            description="Legacy monkeypatched workflow adapter",
            step_labels=dict(DEFAULT_STEP_LABELS),
            generation_nodes=set(DEFAULT_GENERATION_NODES),
            metadata={"classifier": classifier_meta, "retrieval_strategy": "self_corrective"},
        )

    def _call_ask_fn(
        self,
        question: str,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        callback: Any = None,
    ) -> Any:
        if self.ask_fn is None:
            return {"answer": ""}

        kwargs: dict[str, Any] = {
            "session_id": session_id,
            "user_id": user_id,
            "metadata": metadata,
            "callback": callback,
        }

        try:
            sig = inspect.signature(self.ask_fn)
            has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if has_varkw:
                return self.ask_fn(question, **kwargs)
            filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            return self.ask_fn(question, **filtered_kwargs)
        except (ValueError, TypeError):
            try:
                return self.ask_fn(question, **kwargs)
            except TypeError:
                return self.ask_fn(question)

    def get_compiled_graph(self) -> Any:
        adapter_self = self

        class _UnifiedGraph:
            def invoke(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
                if adapter_self.ask_fn is not None and callable(adapter_self.ask_fn):
                    callbacks = (config or {}).get("callbacks", [])
                    callback = callbacks[0] if callbacks else None
                    metadata = (config or {}).get("metadata", {})
                    configurable = (config or {}).get("configurable", {})
                    res = adapter_self._call_ask_fn(
                        question=state.get("question", ""),
                        session_id=configurable.get("thread_id") or state.get("session_id"),
                        user_id=state.get("user_id"),
                        metadata=metadata,
                        callback=callback,
                    )
                    if isinstance(res, dict):
                        return res
                    return {"answer": str(res) if res is not None else ""}

                if adapter_self.graph is not None:
                    if hasattr(adapter_self.graph, "invoke") and callable(adapter_self.graph.invoke):
                        return adapter_self.graph.invoke(state, config=config)
                    if callable(adapter_self.graph):
                        return adapter_self.graph(state, config=config)

                return {"answer": ""}

            def astream_events(self, *args: Any, **kwargs: Any) -> Any:
                if adapter_self.graph is not None and hasattr(adapter_self.graph, "astream_events"):
                    return adapter_self.graph.astream_events(*args, **kwargs)
                raise NotImplementedError("Legacy ask workflow does not implement streaming.")

        return _UnifiedGraph()

    def to_initial_state(self, request: Any, trace_id: str | None = None) -> dict[str, Any]:
        if isinstance(request, dict):
            question = request.get("question", "")
            session_id = request.get("session_id")
            user_id = request.get("user_id")
            req_tid = request.get("trace_id")
            attachments = request.get("attachments") or []
        else:
            question = getattr(request, "question", "")
            session_id = getattr(request, "session_id", None)
            user_id = getattr(request, "user_id", None)
            req_tid = getattr(request, "trace_id", None)
            attachments = getattr(request, "attachments", None) or []

        return {
            "question": question,
            "session_id": session_id,
            "user_id": user_id,
            "trace_id": trace_id or req_tid,
            "attachments": attachments,
        }

    def to_chat_response(self, final_state: dict[str, Any], trace_id: str | None = None) -> ChatResponse:
        from app.api.schemas import ChatResponse

        if isinstance(final_state, ChatResponse):
            return final_state

        if not isinstance(final_state, dict):
            final_state = {"answer": str(final_state)}

        resolved_tid = trace_id or final_state.get("trace_id")
        return ChatResponse(
            workflow=self.workflow_id,
            answer=final_state.get("answer") or "",
            source_used=final_state.get("source_used") or "direct",
            trace=final_state.get("trace") or [],
            citations=final_state.get("citations") or [],
            rewritten_query=final_state.get("current_query") or final_state.get("question"),
            trace_id=resolved_tid,
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        if isinstance(final_state, dict):
            return EvalContext(
                source_used=final_state.get("source_used") or "direct",
                retrieved_contexts=final_state.get("retrieved_contexts") or [],
            )
        return EvalContext(source_used="direct", retrieved_contexts=[])

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": f"Inference error: {exc}",
            "source_used": "direct",
            "trace": [f"Error: {str(exc)}"],
            "citations": [],
            "retrieved_contexts": [],
            "question": current_state.get("question", "") if isinstance(current_state, dict) else "",
        }


def _sync_legacy_registry(workflow_id: str) -> None:
    registry = get_workflow_registry()
    from app.rag import fix_single_llm, fix_single_jev
    from app.rag.workflows import register_default_workflows

    ask_fn = WORKFLOW_ASKS.get(workflow_id)
    graph = WORKFLOW_GRAPHS.get(workflow_id)

    # If both ask_fn and graph are deleted/missing
    if ask_fn is None and graph is None:
        if workflow_id in ("fix_single_llm", "fix_single_jev"):
            if "fix_single_llm" not in WORKFLOW_ASKS:
                super(_LegacyAsksDict, WORKFLOW_ASKS).__setitem__("fix_single_llm", fix_single_llm.ask)
            if "fix_single_llm" not in WORKFLOW_GRAPHS:
                super(_LegacyGraphsDict, WORKFLOW_GRAPHS).__setitem__("fix_single_llm", fix_single_llm.agent_graph)
            if "fix_single_jev" not in WORKFLOW_ASKS:
                super(_LegacyAsksDict, WORKFLOW_ASKS).__setitem__("fix_single_jev", fix_single_jev.ask)
            if "fix_single_jev" not in WORKFLOW_GRAPHS:
                super(_LegacyGraphsDict, WORKFLOW_GRAPHS).__setitem__("fix_single_jev", fix_single_jev.agent_graph)
            register_default_workflows(registry)
        else:
            registry.unregister(workflow_id)
        return

    is_llm_orig = (
        workflow_id == "fix_single_llm"
        and (ask_fn is None or ask_fn is fix_single_llm.ask)
        and (graph is None or graph is fix_single_llm.agent_graph)
    )
    is_jev_orig = (
        workflow_id == "fix_single_jev"
        and (ask_fn is None or ask_fn is fix_single_jev.ask)
        and (graph is None or graph is fix_single_jev.agent_graph)
    )

    if is_llm_orig or is_jev_orig:
        if workflow_id == "fix_single_llm":
            if "fix_single_llm" not in WORKFLOW_ASKS:
                super(_LegacyAsksDict, WORKFLOW_ASKS).__setitem__("fix_single_llm", fix_single_llm.ask)
            if "fix_single_llm" not in WORKFLOW_GRAPHS:
                super(_LegacyGraphsDict, WORKFLOW_GRAPHS).__setitem__("fix_single_llm", fix_single_llm.agent_graph)
        elif workflow_id == "fix_single_jev":
            if "fix_single_jev" not in WORKFLOW_ASKS:
                super(_LegacyAsksDict, WORKFLOW_ASKS).__setitem__("fix_single_jev", fix_single_jev.ask)
            if "fix_single_jev" not in WORKFLOW_GRAPHS:
                super(_LegacyGraphsDict, WORKFLOW_GRAPHS).__setitem__("fix_single_jev", fix_single_jev.agent_graph)
        register_default_workflows(registry)
    else:
        orig_ask = fix_single_llm.ask if workflow_id == "fix_single_llm" else getattr(fix_single_jev, "ask", None)
        effective_ask = ask_fn if ask_fn is not orig_ask else None

        orig_graph = fix_single_llm.agent_graph if workflow_id == "fix_single_llm" else getattr(fix_single_jev, "agent_graph", None)
        effective_graph = graph if graph is not orig_graph else None

        registry.register(
            _LegacyUnifiedWorkflowAdapter(
                workflow_id=workflow_id,
                ask_fn=effective_ask,
                graph=effective_graph or graph,
            ),
            override=True,
        )


class _LegacyAsksDict(dict):
    """Dictionary supporting backward compatibility with tests monkeypatching WORKFLOW_ASKS."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._initialized = False

    def _ensure_defaults(self) -> None:
        if not self._initialized:
            self._initialized = True
            from app.rag import fix_single_llm, fix_single_jev
            if "fix_single_llm" not in self:
                super().__setitem__("fix_single_llm", fix_single_llm.ask)
            if "fix_single_jev" not in self:
                super().__setitem__("fix_single_jev", fix_single_jev.ask)

    def __getitem__(self, key: str) -> Any:
        self._ensure_defaults()
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._ensure_defaults()
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        self._ensure_defaults()
        return super().__contains__(key)

    def __iter__(self):
        self._ensure_defaults()
        return super().__iter__()

    def items(self):
        self._ensure_defaults()
        return super().items()

    def values(self):
        self._ensure_defaults()
        return super().values()

    def keys(self):
        self._ensure_defaults()
        return super().keys()

    def __len__(self) -> int:
        self._ensure_defaults()
        return super().__len__()

    def __setitem__(self, key: str, value: Any) -> None:
        self._ensure_defaults()
        super().__setitem__(key, value)
        _sync_legacy_registry(key)

    def __delitem__(self, key: str) -> None:
        self._ensure_defaults()
        super().__delitem__(key)
        _sync_legacy_registry(key)

    def pop(self, key: str, *args: Any) -> Any:
        self._ensure_defaults()
        res = super().pop(key, *args)
        _sync_legacy_registry(key)
        return res

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._ensure_defaults()
        super().update(*args, **kwargs)
        keys = list(dict(*args, **kwargs).keys())
        for k in keys:
            _sync_legacy_registry(k)


class _LegacyGraphsDict(dict):
    """Dictionary supporting backward compatibility with tests monkeypatching WORKFLOW_GRAPHS."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._initialized = False

    def _ensure_defaults(self) -> None:
        if not self._initialized:
            self._initialized = True
            from app.rag import fix_single_llm, fix_single_jev
            if "fix_single_llm" not in self:
                super().__setitem__("fix_single_llm", fix_single_llm.agent_graph)
            if "fix_single_jev" not in self:
                super().__setitem__("fix_single_jev", fix_single_jev.agent_graph)

    def __getitem__(self, key: str) -> Any:
        self._ensure_defaults()
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._ensure_defaults()
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        self._ensure_defaults()
        return super().__contains__(key)

    def __iter__(self):
        self._ensure_defaults()
        return super().__iter__()

    def items(self):
        self._ensure_defaults()
        return super().items()

    def values(self):
        self._ensure_defaults()
        return super().values()

    def keys(self):
        self._ensure_defaults()
        return super().keys()

    def __len__(self) -> int:
        self._ensure_defaults()
        return super().__len__()

    def __setitem__(self, key: str, value: Any) -> None:
        self._ensure_defaults()
        super().__setitem__(key, value)
        _sync_legacy_registry(key)

    def __delitem__(self, key: str) -> None:
        self._ensure_defaults()
        super().__delitem__(key)
        _sync_legacy_registry(key)

    def pop(self, key: str, *args: Any) -> Any:
        self._ensure_defaults()
        res = super().pop(key, *args)
        _sync_legacy_registry(key)
        return res

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._ensure_defaults()
        super().update(*args, **kwargs)
        keys = list(dict(*args, **kwargs).keys())
        for k in keys:
            _sync_legacy_registry(k)


def reset_legacy_defaults() -> None:
    """Reset WORKFLOW_ASKS, WORKFLOW_GRAPHS, and WorkflowRegistry to canonical defaults."""
    from app.rag import fix_single_llm, fix_single_jev
    from app.rag.workflows import register_default_workflows

    registry = get_workflow_registry()
    super(_LegacyAsksDict, WORKFLOW_ASKS).clear()
    super(_LegacyAsksDict, WORKFLOW_ASKS).update({
        "fix_single_llm": fix_single_llm.ask,
        "fix_single_jev": fix_single_jev.ask,
    })
    WORKFLOW_ASKS._initialized = True
    super(_LegacyGraphsDict, WORKFLOW_GRAPHS).clear()
    super(_LegacyGraphsDict, WORKFLOW_GRAPHS).update({
        "fix_single_llm": fix_single_llm.agent_graph,
        "fix_single_jev": fix_single_jev.agent_graph,
    })
    WORKFLOW_GRAPHS._initialized = True
    register_default_workflows(registry)


WORKFLOW_ASKS: _LegacyAsksDict = _LegacyAsksDict()

WORKFLOW_GRAPHS: _LegacyGraphsDict = _LegacyGraphsDict()

__all__ = [
    "_LegacyUnifiedWorkflowAdapter",
    "_sync_legacy_registry",
    "_LegacyAsksDict",
    "_LegacyGraphsDict",
    "WORKFLOW_ASKS",
    "WORKFLOW_GRAPHS",
    "reset_legacy_defaults",
]
