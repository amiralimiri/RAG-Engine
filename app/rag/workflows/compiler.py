"""Declarative YAML workflow manifest schema and LangGraph compiler.

The compiler turns concise YAML workflow manifests into fully functional
``WorkflowDefinition`` implementations. It resolves declarative step aliases through
the :class:`~app.rag.steps.base.StepCatalog`, instantiates each step with injected
dependencies (tools, chat models, prompt stores, classifiers), assembles a
``StateGraph(AgentState)``, and synthesizes dynamic handoff routing so agent steps can
transfer control without bespoke router functions.

The schema is intentionally strict: unknown fields, malformed dotted paths, undeclared
nodes, and invalid edges fail fast with descriptive validation errors before a graph is
ever compiled.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Callable, Hashable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import structlog
import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.concurrency import wrap_safe_invoke
from app.rag.prompts import PromptStore, get_prompt_store
from app.rag.state import AgentState
from app.rag.steps import AgentStep, PromptGenerationStep, StepCatalog, WorkflowStep, get_step_catalog
from app.rag.tools import create_retriever_tool, create_web_search_tool
from app.rag.workflows.base import PluggableWorkflow, WorkflowInfo, WorkflowInput, WorkflowOutput

logger = structlog.get_logger(__name__)

TERMINAL_TARGETS: frozenset[str] = frozenset({"finish", "end"})
"""Handoff/edge targets that terminate execution rather than routing to a node."""

_RESERVED_NODE_NAMES: frozenset[str] = frozenset({"start", "end", "finish", "__start__", "__end__"})

_DOTTED_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_IMPORT_PATH_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*"
    r"(?::[A-Za-z_][A-Za-z0-9_]*)?$"
)


class WorkflowValidationError(ValueError):
    """Raised when a declarative manifest cannot be compiled into a valid workflow."""


# ---------------------------------------------------------------------------
# Resolver input types
# ---------------------------------------------------------------------------

ToolResolver = BaseTool | Mapping[str, BaseTool] | Callable[[str], BaseTool]
ModelResolver = BaseChatModel | Mapping[str, BaseChatModel] | Callable[..., BaseChatModel]
ClassifierResolver = Any | Mapping[str, Any] | Callable[..., Any]
ConditionResolver = Mapping[str, Callable[..., str]] | Callable[[str], Callable[..., str]]


def _validate_dotted_path(value: str, *, field_name: str) -> str:
    """Validate a dot-notated blackboard path such as ``context.agent.query``."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty dot-notated path.")
    if not _DOTTED_PATH_RE.fullmatch(cleaned):
        raise ValueError(
            f"{field_name} contains invalid dot path '{value}'. Expected segments like "
            "'context.agent.query' (letters, digits, underscores, separated by single dots)."
        )
    return cleaned


def _validate_import_path(value: str, *, field_name: str) -> str:
    """Validate a registered alias or dynamic Python import path."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string.")
    # Plain aliases are free-form catalog keys; only dotted/colon identifiers are imports.
    if ":" in cleaned or "." in cleaned:
        if not _IMPORT_PATH_RE.fullmatch(cleaned):
            raise ValueError(
                f"{field_name} contains invalid dynamic import path '{value}'. Expected "
                "'package.module:ClassName' or 'package.module.ClassName'."
            )
    return cleaned


def _normalize_terminal(target: str) -> str:
    """Return the canonical lowercase form for potential terminal edge targets."""
    return target.strip().lower()


def _is_terminal_target(target: str) -> bool:
    """Return True when an edge or handoff target terminates the workflow."""
    return _normalize_terminal(target) in TERMINAL_TARGETS


class StepConfig(BaseModel):
    """Declarative configuration for a single workflow graph node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(description="Registered step alias or dotted path 'pkg.module:Class'")
    prompt: str | None = Field(default=None, description="Prompt key rendered by model-backed steps")
    tools: list[str] = Field(default_factory=list, description="Tool names resolved through the tool resolver")
    model: str | None = Field(default=None, description="Model name resolved through the model resolver")
    temperature: float | None = Field(default=None, description="Optional model sampling temperature")
    classifier: str | None = Field(
        default=None,
        description="Optional classifier name resolved through the classifier resolver",
    )
    inputs: dict[str, str] = Field(default_factory=dict, description="Logical input keys mapped to state paths")
    outputs: dict[str, str] = Field(default_factory=dict, description="Logical output keys mapped to state paths")
    handoffs: list[str] = Field(default_factory=list, description="Agent handoff targets or terminal signals")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments passed to the resolved step constructor",
    )

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        return _validate_import_path(value, field_name="Step type")

    @field_validator("prompt", "model", "classifier")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tool_name in value:
            name = tool_name.strip()
            if not name:
                raise ValueError("Tool names must be non-empty strings.")
            cleaned.append(name)
        return cleaned

    @field_validator("handoffs")
    @classmethod
    def _validate_handoffs(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for target in value:
            name = target.strip()
            if not name:
                raise ValueError("Handoff targets must be non-empty strings.")
            cleaned.append(name)
        return cleaned

    @field_validator("inputs", "outputs")
    @classmethod
    def _validate_mapping_paths(cls, value: dict[str, str], info: Any) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for logical_key, path in value.items():
            key = logical_key.strip()
            if not key:
                raise ValueError(f"{info.field_name} keys must be non-empty strings.")
            cleaned[key] = _validate_dotted_path(path, field_name=f"{info.field_name}['{key}']")
        return cleaned


class EdgeConfig(BaseModel):
    """Declarative graph edge, either direct or condition-routed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_node: str = Field(description="Source node name or 'START'")
    to_node: str | None = Field(default=None, description="Direct destination node or terminal target")
    condition: str | None = Field(
        default=None,
        description="Built-in condition name or Python dotted path resolving to a router callable",
    )
    targets: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from router return value to destination node or terminal target",
    )

    @field_validator("from_node", "to_node", "condition", mode="before")
    @classmethod
    def _strip_optional_fields(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @field_validator("condition")
    @classmethod
    def _validate_condition(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_import_path(value, field_name="Edge condition")

    @field_validator("targets")
    @classmethod
    def _validate_targets(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for route_key, target in value.items():
            key = str(route_key).strip()
            destination = target.strip() if isinstance(target, str) else ""
            if not key:
                raise ValueError("Conditional edge target keys must be non-empty strings.")
            if not destination:
                raise ValueError(f"Conditional edge target '{key}' must be a non-empty node name.")
            cleaned[key] = destination
        return cleaned

    @model_validator(mode="after")
    def _validate_shape(self) -> EdgeConfig:
        if not self.from_node:
            raise ValueError("Edge from_node must be a non-empty node name or 'START'.")

        if self.condition is None:
            if self.to_node is None:
                raise ValueError(f"Edge from '{self.from_node}' must declare either to_node or a condition.")
            if self.targets:
                raise ValueError(f"Edge from '{self.from_node}' declares targets without a condition.")
        else:
            if not self.targets:
                raise ValueError(f"Conditional edge from '{self.from_node}' must declare a non-empty targets mapping.")
            if self.to_node is not None:
                raise ValueError(
                    f"Conditional edge from '{self.from_node}' cannot also declare to_node; "
                    "use targets for conditional destinations."
                )
        return self


class WorkflowManifest(BaseModel):
    """Complete declarative workflow topology parsed from YAML or a dictionary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(description="Unique workflow slug")
    name: str = Field(description="Human-readable workflow name")
    description: str = Field(default="", description="Short explanation of workflow purpose")
    is_conversational: bool = Field(default=False, description="Whether the workflow maintains history")
    supports_streaming: bool = Field(default=True, description="Whether token streaming is supported")
    entrypoint_node: str = Field(default="START", description="Entry node name or 'START'")
    generation_nodes: list[str] = Field(
        default_factory=list,
        description="Graph nodes whose model output should stream to clients",
    )
    step_labels: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from graph node names to UI progression labels",
    )
    nodes: dict[str, StepConfig] = Field(description="Declared graph nodes keyed by node name")
    edges: list[EdgeConfig] = Field(default_factory=list, description="Declared graph edges")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary workflow metadata")

    @field_validator("id", "name", "entrypoint_node", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("id", "name")
    @classmethod
    def _require_non_empty(cls, value: str, info: Any) -> str:
        if not value:
            raise ValueError(f"Workflow {info.field_name} must be a non-empty string.")
        return value

    @field_validator("entrypoint_node")
    @classmethod
    def _validate_entrypoint_text(cls, value: str) -> str:
        if not value:
            raise ValueError("Workflow entrypoint_node must be a non-empty node name or 'START'.")
        return value

    @field_validator("nodes")
    @classmethod
    def _validate_nodes(cls, value: dict[str, StepConfig]) -> dict[str, StepConfig]:
        if not value:
            raise ValueError("Workflow manifest must declare at least one node.")
        cleaned: dict[str, StepConfig] = {}
        for raw_name, config in value.items():
            name = raw_name.strip() if isinstance(raw_name, str) else str(raw_name).strip()
            if not name:
                raise ValueError("Workflow node names must be non-empty strings.")
            if _normalize_terminal(name) in _RESERVED_NODE_NAMES:
                raise ValueError(f"Node name '{name}' is reserved for workflow start/termination routing.")
            if name in cleaned:
                raise ValueError(f"Duplicate workflow node name '{name}'.")
            cleaned[name] = config
        return cleaned

    @field_validator("generation_nodes")
    @classmethod
    def _validate_generation_nodes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw_name in value:
            name = raw_name.strip()
            if not name:
                raise ValueError("Generation node names must be non-empty strings.")
            cleaned.append(name)
        return cleaned

    @field_validator("step_labels")
    @classmethod
    def _validate_step_labels(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for raw_name, label in value.items():
            name = raw_name.strip() if isinstance(raw_name, str) else str(raw_name).strip()
            if not name:
                raise ValueError("step_labels keys must be non-empty node names.")
            cleaned[name] = str(label)
        return cleaned

    @model_validator(mode="after")
    def _validate_topology(self) -> WorkflowManifest:
        node_names = set(self.nodes)

        entrypoint = self.entrypoint_node
        if entrypoint.upper() != "START" and entrypoint not in node_names:
            raise ValueError(
                f"Workflow entrypoint_node '{entrypoint}' is not a declared node. "
                f"Available nodes: {sorted(node_names)}."
            )

        unknown_generation = sorted(set(self.generation_nodes) - node_names)
        if unknown_generation:
            raise ValueError(
                f"generation_nodes references undeclared node(s): {unknown_generation}. "
                f"Available nodes: {sorted(node_names)}."
            )

        unknown_labels = sorted(set(self.step_labels) - node_names)
        if unknown_labels:
            raise ValueError(
                f"step_labels references undeclared node(s): {unknown_labels}. Available nodes: {sorted(node_names)}."
            )

        for node_name, config in self.nodes.items():
            for target in config.handoffs:
                if target.lower() in TERMINAL_TARGETS:
                    continue
                if target == node_name:
                    raise ValueError(
                        f"Node '{node_name}' cannot hand off to itself; handoff routing would loop forever."
                    )
                if target not in node_names:
                    raise ValueError(
                        f"Node '{node_name}' declares handoff to undeclared node '{target}'. "
                        f"Available nodes: {sorted(node_names)}."
                    )

        for index, edge in enumerate(self.edges):
            source = edge.from_node
            if source.upper() != "START" and source not in node_names:
                raise ValueError(
                    f"Edge #{index} from_node '{source}' is not a declared node or 'START'. "
                    f"Available nodes: {sorted(node_names)}."
                )

            if edge.to_node is not None:
                self._validate_edge_target(
                    target=edge.to_node,
                    edge_index=index,
                    node_names=node_names,
                    label="to_node",
                )

            for route_key, target in edge.targets.items():
                self._validate_edge_target(
                    target=target,
                    edge_index=index,
                    node_names=node_names,
                    label=f"targets['{route_key}']",
                )

        self._validate_no_duplicate_edges()
        return self

    @staticmethod
    def _validate_edge_target(
        *,
        target: str,
        edge_index: int,
        node_names: set[str],
        label: str,
    ) -> None:
        if _is_terminal_target(target):
            return
        if target not in node_names:
            raise ValueError(
                f"Edge #{edge_index} {label} references undeclared node '{target}'. "
                f"Available nodes: {sorted(node_names)}."
            )

    def _validate_no_duplicate_edges(self) -> None:
        seen: set[tuple[str, str | None, tuple[tuple[str, str], ...]]] = set()
        for edge in self.edges:
            key = (edge.from_node, edge.condition, tuple(sorted(edge.targets.items())))
            if key in seen:
                raise ValueError(f"Duplicate edge declared from '{edge.from_node}'.")
            seen.add(key)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkflowManifest:
        """Validate and construct a manifest from a mapping."""
        if not isinstance(data, Mapping):
            raise WorkflowValidationError(f"Workflow manifest must be a mapping, got {type(data).__name__}.")
        return cls.model_validate(dict(data))

    @classmethod
    def from_yaml(cls, yaml_text: str) -> WorkflowManifest:
        """Parse and validate a manifest from a YAML document string."""
        try:
            loaded = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise WorkflowValidationError(f"Invalid workflow YAML: {exc}") from exc

        if loaded is None:
            raise WorkflowValidationError("Workflow YAML manifest is empty.")
        return cls.from_dict(loaded)

    @classmethod
    def from_file(cls, path: Path | str) -> WorkflowManifest:
        """Parse and validate a manifest from a YAML file."""
        manifest_path = Path(path)
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Workflow manifest file not found: '{manifest_path}'.")
        return cls.from_yaml(manifest_path.read_text(encoding="utf-8"))


class CompiledDeclarativeWorkflow(PluggableWorkflow):
    """Compiled LangGraph workflow produced by :class:`WorkflowCompiler`.

    Inherits the standard domain state/output conversions from
    :class:`~app.rag.workflows.base.PluggableWorkflow` while exposing the originating
    manifest and augmenting initial state with the canonical blackboard keys used by
    declarative steps.
    """

    def __init__(
        self,
        info: WorkflowInfo,
        graph: Any,
        manifest: WorkflowManifest,
    ) -> None:
        super().__init__(info, graph)
        self.manifest = manifest

    def to_initial_state(
        self,
        input: WorkflowInput | dict[str, Any] | Any,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        state = super().to_initial_state(input, trace_id)
        question = str(state.get("question") or "")
        state.setdefault("current_query", question)
        state.setdefault("context", {})
        state.setdefault("active_agent", None)
        state.setdefault("retry_count", 0)
        state.setdefault("kb_docs", [])
        state.setdefault("web_results", "")
        state.setdefault("kb_grade", "")
        state.setdefault("web_grade", "")
        return state

    def to_workflow_output(
        self,
        final_state: dict[str, Any],
        trace_id: str | None = None,
    ) -> WorkflowOutput:
        output = super().to_workflow_output(final_state, trace_id)
        if not output.rewritten_query:
            output.rewritten_query = final_state.get("current_query") or final_state.get("question")
        raw_contexts = final_state.get("retrieved_contexts") or []
        if isinstance(raw_contexts, str):
            output.retrieved_contexts = [raw_contexts]
        elif isinstance(raw_contexts, Sequence):
            output.retrieved_contexts = [str(context) for context in raw_contexts]
        return output


def _invoke_with_optional_args(
    func: Callable[..., Any],
    *args: Any,
) -> Any:
    """Invoke a resolver callable while tolerating narrower arities.

    User-supplied resolvers commonly accept either ``(name)`` or ``(name, temperature)``.
    Inspecting the signature keeps the public seam ergonomic without forcing adapters.
    """
    try:
        signature = inspect.signature(func)
    except TypeError, ValueError:
        return func(*args)

    parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    has_varargs = any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    if has_varargs:
        return func(*args)
    return func(*args[: len(parameters)])


def _default_model_resolver(name: str | None, temperature: float | None) -> BaseChatModel:
    """Construct a production chat model when no explicit resolver is provided."""
    from langchain_openai import ChatOpenAI

    from app.core.config import get_settings

    settings = get_settings()
    return ChatOpenAI(
        model=name or settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=temperature if temperature is not None else 0,
        stream_usage=True,
    )


def _default_tool_resolver(name: str) -> BaseTool:
    """Resolve well-known canonical tool names to their production factories."""
    normalized = name.strip().lower()
    if normalized in {
        "kb",
        "kb_tool",
        "knowledge_base",
        "knowledge_base_search",
        "retriever",
        "retrieval",
    }:
        return cast(BaseTool, create_retriever_tool())
    if normalized in {"web", "web_tool", "web_search", "search", "tavily"}:
        return cast(BaseTool, create_web_search_tool())
    raise WorkflowValidationError(
        f"Unknown tool '{name}'. Provide a tool_resolver mapping (for example "
        "{{'my_tool': my_tool_instance}}) or use a canonical name such as 'kb_tool' "
        "or 'web_search'."
    )


def _default_condition_resolver(name: str) -> Callable[..., str]:
    """Resolve built-in or dynamic Python router functions."""
    builtin_name = name.strip()
    if builtin_name not in {"route_start", "route_after_router", "after_kb", "after_web"}:
        return _import_condition_callable(builtin_name)

    # Imported lazily so the compiler module stays independent at import time.
    from app.rag import engine

    condition = getattr(engine, builtin_name)
    if not callable(condition):
        raise WorkflowValidationError(f"Built-in condition '{builtin_name}' is not callable.")
    return cast(Callable[..., str], condition)


def _import_condition_callable(identifier: str) -> Callable[..., str]:
    """Import a custom router callable from 'module:function' or 'module.function'."""
    if ":" in identifier:
        module_name, attribute_name = identifier.split(":", 1)
    elif "." in identifier:
        module_name, attribute_name = identifier.rsplit(".", 1)
    else:
        raise WorkflowValidationError(
            f"Unknown condition '{identifier}'. Use a built-in condition name or a dotted "
            "Python path such as 'my_module.routers:route_next'."
        )

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - surfaced as a validation error
        raise WorkflowValidationError(f"Could not import condition module '{module_name}': {exc}") from exc

    if not hasattr(module, attribute_name):
        raise WorkflowValidationError(f"Condition module '{module_name}' has no attribute '{attribute_name}'.")

    condition = getattr(module, attribute_name)
    if not callable(condition):
        raise WorkflowValidationError(f"Resolved condition '{identifier}' is not callable.")
    return cast(Callable[..., str], condition)


class WorkflowCompiler:
    """Compile declarative YAML manifests into executable ``WorkflowDefinition`` objects.

    Dependencies are injected through resolvers rather than global lookups:

    - ``tool_resolver`` maps manifest tool names to LangChain/RAG tool instances.
    - ``model_resolver`` maps model names (and temperatures) to chat models.
    - ``prompt_store`` supplies prompt rendering and observation metadata.
    - ``classifier_resolver`` supplies :class:`WorkflowClassifier` adapters.
    - ``condition_resolver`` maps explicit edge conditions to router callables.

    Resolvers may be mappings, callables, or (for single-dependency manifests) direct
    instances. They can be supplied at construction or overridden per ``compile`` call.
    """

    def __init__(
        self,
        *,
        catalog: StepCatalog | None = None,
        tool_resolver: ToolResolver | None = None,
        model_resolver: ModelResolver | None = None,
        prompt_store: PromptStore | None = None,
        classifier: Any | None = None,
        classifier_resolver: ClassifierResolver | None = None,
        condition_resolver: ConditionResolver | None = None,
    ) -> None:
        self.catalog = catalog if catalog is not None else get_step_catalog()
        self.tool_resolver = tool_resolver
        self.model_resolver = model_resolver
        self.prompt_store = prompt_store
        self.classifier = classifier
        self.classifier_resolver = classifier_resolver
        self.condition_resolver = condition_resolver

    # ------------------------------------------------------------------
    # Public compilation entrypoints
    # ------------------------------------------------------------------

    def compile(
        self,
        manifest: WorkflowManifest | Mapping[str, Any] | str | Path,
        *,
        tool_resolver: ToolResolver | None = None,
        model_resolver: ModelResolver | None = None,
        prompt_store: PromptStore | None = None,
        classifier: Any | None = None,
        classifier_resolver: ClassifierResolver | None = None,
        condition_resolver: ConditionResolver | None = None,
    ) -> CompiledDeclarativeWorkflow:
        """Compile a manifest into a cached, executable declarative workflow."""
        resolved_manifest = self._coerce_manifest(manifest)
        active_tool_resolver = tool_resolver if tool_resolver is not None else self.tool_resolver
        active_model_resolver = model_resolver if model_resolver is not None else self.model_resolver
        active_prompt_store = prompt_store if prompt_store is not None else self.prompt_store
        active_classifier = classifier if classifier is not None else self.classifier
        active_classifier_resolver = (
            classifier_resolver if classifier_resolver is not None else self.classifier_resolver
        )
        active_condition_resolver = condition_resolver if condition_resolver is not None else self.condition_resolver

        # Resolve all step classes before touching the graph so unknown aliases fail fast.
        step_types = self._resolve_step_types(resolved_manifest)

        outbound_sources: set[str] = set()
        explicit_start_edges: list[EdgeConfig] = []
        handoff_unconditional_edges: dict[str, list[EdgeConfig]] = {}

        # Partition and validate explicit edges before instantiating any dependencies.
        for edge in resolved_manifest.edges:
            if edge.from_node.upper() == "START":
                explicit_start_edges.append(edge)
            elif edge.condition is None and resolved_manifest.nodes[edge.from_node].handoffs:
                handoff_unconditional_edges.setdefault(edge.from_node, []).append(edge)
            elif edge.condition is not None and resolved_manifest.nodes[edge.from_node].handoffs:
                raise WorkflowValidationError(
                    f"Node '{edge.from_node}' declares both handoffs and an explicit conditional "
                    "edge. Handoff routing is synthesized automatically; remove the explicit "
                    "conditional edge or the handoffs list."
                )

        graph: StateGraph[AgentState] = StateGraph(AgentState)

        # Instantiate every node and attach it to the graph.
        for node_name, step_config in resolved_manifest.nodes.items():
            step_type = step_types[node_name]
            step = self._instantiate_step(
                node_name=node_name,
                step_config=step_config,
                step_type=step_type,
                tool_resolver=active_tool_resolver,
                model_resolver=active_model_resolver,
                prompt_store=active_prompt_store,
                classifier=active_classifier,
                classifier_resolver=active_classifier_resolver,
            )
            graph.add_node(node_name, cast(Any, step))

        # Wire START entry routing.
        if explicit_start_edges:
            for edge in explicit_start_edges:
                self._wire_edge(
                    graph=graph,
                    edge=edge,
                    condition_resolver=active_condition_resolver,
                )
                outbound_sources.add("START")
        else:
            entrypoint = self._resolve_entrypoint(resolved_manifest)
            graph.add_edge(START, entrypoint)
            outbound_sources.add("START")

        # Wire ordinary explicit edges.
        for edge in resolved_manifest.edges:
            if edge.from_node.upper() == "START":
                continue
            if edge.condition is None and edge.from_node in handoff_unconditional_edges:
                continue
            self._wire_edge(
                graph=graph,
                edge=edge,
                condition_resolver=active_condition_resolver,
            )
            outbound_sources.add(edge.from_node)

        # Synthesize dynamic handoff routing for agent-style nodes.
        for node_name, step_config in resolved_manifest.nodes.items():
            if not step_config.handoffs:
                continue

            fallback_edges = handoff_unconditional_edges.get(node_name, [])
            if len(fallback_edges) > 1:
                raise WorkflowValidationError(
                    f"Node '{node_name}' declares multiple explicit fallback edges; "
                    "handoff routing can use at most one."
                )
            fallback_target = fallback_edges[0].to_node or END if fallback_edges else END
            if fallback_target != END and _is_terminal_target(fallback_target):
                fallback_target = END

            path_map, router = self._build_handoff_router(
                node_name=node_name,
                handoffs=step_config.handoffs,
                fallback_target=cast(str, fallback_target),
            )
            graph.add_conditional_edges(node_name, router, path_map)
            outbound_sources.add(node_name)

        # Any node without an outgoing edge terminates the workflow.
        for node_name in resolved_manifest.nodes:
            if node_name not in outbound_sources:
                graph.add_edge(node_name, END)

        compiled_graph = wrap_safe_invoke(graph.compile())

        info = self._build_workflow_info(resolved_manifest, step_types)
        logger.info(
            "declarative_workflow_compiled",
            workflow_id=resolved_manifest.id,
            nodes=list(resolved_manifest.nodes),
            edges=len(resolved_manifest.edges),
        )
        return CompiledDeclarativeWorkflow(
            info=info,
            graph=compiled_graph,
            manifest=resolved_manifest,
        )

    def compile_yaml(
        self,
        yaml_source: str | Path,
        *,
        tool_resolver: ToolResolver | None = None,
        model_resolver: ModelResolver | None = None,
        prompt_store: PromptStore | None = None,
        classifier: Any | None = None,
        classifier_resolver: ClassifierResolver | None = None,
        condition_resolver: ConditionResolver | None = None,
    ) -> CompiledDeclarativeWorkflow:
        """Parse a YAML string or file path and compile the resulting workflow."""
        manifest = self.load_manifest(yaml_source)
        return self.compile(
            manifest,
            tool_resolver=tool_resolver,
            model_resolver=model_resolver,
            prompt_store=prompt_store,
            classifier=classifier,
            classifier_resolver=classifier_resolver,
            condition_resolver=condition_resolver,
        )

    def load_manifest(self, yaml_source: str | Path) -> WorkflowManifest:
        """Load a manifest from a path, or parse an inline YAML string."""
        if isinstance(yaml_source, Path):
            return WorkflowManifest.from_file(yaml_source)

        source = yaml_source
        if not isinstance(source, str):
            raise WorkflowValidationError(f"YAML source must be a string or Path, got {type(source).__name__}.")

        stripped = source.strip()
        if not stripped:
            raise WorkflowValidationError("Workflow YAML source is empty.")

        looks_like_path = (
            "\n" not in stripped
            and not stripped.startswith(("{", "[", "-"))
            and stripped.lower().endswith((".yaml", ".yml"))
        )
        if looks_like_path:
            return WorkflowManifest.from_file(stripped)

        try:
            path = Path(stripped)
            if path.is_file():
                return WorkflowManifest.from_file(path)
        except OSError:
            pass

        return WorkflowManifest.from_yaml(source)

    # ------------------------------------------------------------------
    # Manifest and step resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_manifest(
        manifest: WorkflowManifest | Mapping[str, Any] | str | Path,
    ) -> WorkflowManifest:
        if isinstance(manifest, WorkflowManifest):
            return manifest
        if isinstance(manifest, Mapping):
            return WorkflowManifest.from_dict(manifest)
        if isinstance(manifest, Path):
            return WorkflowManifest.from_file(manifest)
        if isinstance(manifest, str):
            stripped = manifest.strip()
            if stripped and "\n" not in stripped and stripped.lower().endswith((".yaml", ".yml")):
                return WorkflowManifest.from_file(stripped)
            return WorkflowManifest.from_yaml(manifest)
        raise WorkflowValidationError(f"Unsupported manifest type: {type(manifest).__name__}.")

    def _resolve_step_types(
        self,
        manifest: WorkflowManifest,
    ) -> dict[str, type[WorkflowStep]]:
        resolved: dict[str, type[WorkflowStep]] = {}
        failures: list[str] = []

        for node_name, step_config in manifest.nodes.items():
            try:
                resolved[node_name] = self.catalog.resolve_step_type(step_config.type)
            except (KeyError, ImportError, AttributeError, TypeError, ValueError) as exc:
                failures.append(f"node '{node_name}' type '{step_config.type}': {exc}")

        if failures:
            raise WorkflowValidationError("Failed to resolve workflow step type(s): " + "; ".join(failures))
        return resolved

    def _resolve_entrypoint(self, manifest: WorkflowManifest) -> str:
        if manifest.entrypoint_node.upper() != "START":
            return manifest.entrypoint_node
        return next(iter(manifest.nodes))

    def _instantiate_step(
        self,
        *,
        node_name: str,
        step_config: StepConfig,
        step_type: type[WorkflowStep],
        tool_resolver: ToolResolver | None,
        model_resolver: ModelResolver | None,
        prompt_store: PromptStore | None,
        classifier: Any | None,
        classifier_resolver: ClassifierResolver | None,
    ) -> WorkflowStep:
        try:
            signature = inspect.signature(step_type.__init__)
        except (TypeError, ValueError) as exc:
            raise WorkflowValidationError(
                f"Could not inspect constructor for step '{step_type.__name__}' on node '{node_name}': {exc}"
            ) from exc

        parameters = {name: parameter for name, parameter in signature.parameters.items() if name != "self"}

        kwargs: dict[str, Any] = {name: value for name, value in step_config.parameters.items() if name != "self"}

        if "name" in parameters:
            kwargs["name"] = node_name
        if "inputs" in parameters:
            kwargs["inputs"] = dict(step_config.inputs) or None
        if "outputs" in parameters:
            kwargs["outputs"] = dict(step_config.outputs) or None

        prompt_key = step_config.prompt or step_config.parameters.get("prompt_key")
        if "prompt_key" in parameters and prompt_key is not None:
            kwargs["prompt_key"] = str(prompt_key)

        if "prompt_store" in parameters:
            kwargs["prompt_store"] = prompt_store or get_prompt_store()

        if "handoffs" in parameters:
            kwargs["handoffs"] = list(step_config.handoffs) or None

        if "temperature" in parameters and step_config.temperature is not None:
            kwargs["temperature"] = step_config.temperature

        if "tool" in parameters:
            kwargs["tool"] = self._resolve_single_tool(
                node_name=node_name,
                step_config=step_config,
                resolver=tool_resolver,
            )

        if "tools" in parameters:
            raw_tools = kwargs.get("tools", step_config.tools)
            kwargs["tools"] = self._resolve_tool_list(
                candidate_names=raw_tools,
                resolver=tool_resolver,
                node_name=node_name,
            )

        if "model" in parameters:
            kwargs["model"] = self._resolve_model(
                node_name=node_name,
                step_config=step_config,
                resolver=model_resolver,
                raw_override=kwargs.get("model"),
            )

        if "classifier" in parameters:
            kwargs["classifier"] = self._resolve_classifier(
                node_name=node_name,
                step_config=step_config,
                classifier=classifier,
                resolver=classifier_resolver,
                raw_override=kwargs.get("classifier"),
            )

        missing: list[str] = []
        for name, parameter in parameters.items():
            if name in kwargs:
                continue
            if parameter.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            if parameter.default is inspect.Parameter.empty:
                missing.append(name)

        if missing:
            raise WorkflowValidationError(
                f"Node '{node_name}' step '{step_type.__name__}' requires unresolved "
                f"constructor dependency/dependencies: {sorted(missing)}. Provide them through "
                "the manifest parameters or an appropriate resolver."
            )

        try:
            step = step_type(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalized to a validation error
            raise WorkflowValidationError(
                f"Failed to instantiate node '{node_name}' with step type '{step_type.__name__}': {exc}"
            ) from exc

        if not isinstance(step, WorkflowStep):
            raise WorkflowValidationError(
                f"Node '{node_name}' resolved to {type(step).__name__}, which does not satisfy "
                "the WorkflowStep protocol."
            )
        return step

    # ------------------------------------------------------------------
    # Dependency resolvers
    # ------------------------------------------------------------------

    def _resolve_single_tool(
        self,
        *,
        node_name: str,
        step_config: StepConfig,
        resolver: ToolResolver | None,
    ) -> BaseTool:
        parameters = step_config.parameters
        raw_override = parameters.get("tool")
        if isinstance(raw_override, BaseTool):
            return raw_override
        if isinstance(raw_override, str):
            return self._resolve_tool(raw_override, resolver)

        if not step_config.tools:
            raise WorkflowValidationError(
                f"Node '{node_name}' requires a tool but declares no tools. Add a 'tools' "
                "list to the manifest or set parameters.tool."
            )
        if len(step_config.tools) != 1:
            raise WorkflowValidationError(
                f"Node '{node_name}' requires exactly one tool via the 'tool' constructor "
                f"argument but declares {len(step_config.tools)}: {step_config.tools}."
            )
        return self._resolve_tool(step_config.tools[0], resolver)

    def _resolve_tool_list(
        self,
        *,
        candidate_names: Any,
        resolver: ToolResolver | None,
        node_name: str,
    ) -> list[BaseTool]:
        if isinstance(candidate_names, BaseTool):
            return [candidate_names]
        if not isinstance(candidate_names, Sequence) or isinstance(candidate_names, (str, bytes)):
            raise WorkflowValidationError(f"Node '{node_name}' tools must be a list of tool names or tool instances.")

        resolved: list[BaseTool] = []
        for candidate in candidate_names:
            if isinstance(candidate, BaseTool):
                resolved.append(candidate)
            elif isinstance(candidate, str):
                resolved.append(self._resolve_tool(candidate, resolver))
            else:
                raise WorkflowValidationError(
                    f"Node '{node_name}' tool entries must be strings or BaseTool instances, "
                    f"got {type(candidate).__name__}."
                )
        return resolved

    def _resolve_tool(self, name: str, resolver: ToolResolver | None) -> BaseTool:
        if resolver is None:
            return _default_tool_resolver(name)

        if isinstance(resolver, BaseTool):
            return resolver

        if isinstance(resolver, Mapping):
            if name in resolver:
                tool = resolver[name]
            else:
                folded = {str(key).casefold(): value for key, value in resolver.items()}
                tool = folded.get(name.casefold())
            if tool is None:
                raise WorkflowValidationError(
                    f"Unknown tool '{name}'. Available resolver tools: {sorted(map(str, resolver))}."
                )
            return tool

        if callable(resolver):
            tool = _invoke_with_optional_args(resolver, name)
            if not isinstance(tool, BaseTool):
                raise WorkflowValidationError(
                    f"Tool resolver returned {type(tool).__name__} for '{name}', expected a BaseTool."
                )
            return tool

        raise WorkflowValidationError(f"Unsupported tool_resolver type: {type(resolver).__name__}.")

    def _resolve_model(
        self,
        *,
        node_name: str,
        step_config: StepConfig,
        resolver: ModelResolver | None,
        raw_override: Any = None,
    ) -> BaseChatModel:
        if isinstance(raw_override, BaseChatModel):
            return raw_override

        model_name = step_config.model
        if model_name is None and isinstance(raw_override, str):
            model_name = raw_override
        if model_name is None and isinstance(step_config.parameters.get("model"), str):
            model_name = cast(str, step_config.parameters["model"])

        temperature = step_config.temperature
        if resolver is None:
            return _default_model_resolver(model_name, temperature)

        if isinstance(resolver, BaseChatModel):
            return resolver

        if isinstance(resolver, Mapping):
            if model_name is not None and model_name in resolver:
                return resolver[model_name]
            folded = {str(key).casefold(): value for key, value in resolver.items()}
            if model_name is not None and model_name.casefold() in folded:
                return folded[model_name.casefold()]
            if model_name is None:
                if "default" in folded:
                    return folded["default"]
                if len(resolver) == 1:
                    return next(iter(resolver.values()))
            raise WorkflowValidationError(
                f"Unknown model '{model_name}' for node '{node_name}'. "
                f"Available resolver models: {sorted(map(str, resolver))}."
            )

        if callable(resolver):
            model = _invoke_with_optional_args(resolver, model_name, temperature)
            if not isinstance(model, BaseChatModel):
                raise WorkflowValidationError(
                    f"Model resolver returned {type(model).__name__} for node '{node_name}', expected a BaseChatModel."
                )
            return model

        raise WorkflowValidationError(f"Unsupported model_resolver type: {type(resolver).__name__}.")

    def _resolve_classifier(
        self,
        *,
        node_name: str,
        step_config: StepConfig,
        classifier: Any | None,
        resolver: ClassifierResolver | None,
        raw_override: Any = None,
    ) -> Any:
        if raw_override is not None and not isinstance(raw_override, str):
            return raw_override
        if isinstance(step_config.parameters.get("classifier"), str):
            classifier_name: str | None = cast(str, step_config.parameters["classifier"])
        else:
            classifier_name = step_config.classifier or (raw_override if isinstance(raw_override, str) else None)

        if resolver is not None:
            if isinstance(resolver, Mapping):
                if classifier_name is None:
                    if "default" in resolver:
                        return resolver["default"]
                    if len(resolver) == 1:
                        return next(iter(resolver.values()))
                    raise WorkflowValidationError(
                        f"Node '{node_name}' requires a classifier but does not name one. "
                        f"Available resolver classifiers: {sorted(map(str, resolver))}."
                    )
                if classifier_name in resolver:
                    return resolver[classifier_name]
                folded = {str(key).casefold(): value for key, value in resolver.items()}
                if classifier_name.casefold() in folded:
                    return folded[classifier_name.casefold()]
                raise WorkflowValidationError(
                    f"Unknown classifier '{classifier_name}' for node '{node_name}'. "
                    f"Available resolver classifiers: {sorted(map(str, resolver))}."
                )

            if callable(resolver) and not hasattr(resolver, "route"):
                return _invoke_with_optional_args(resolver, classifier_name)

            return resolver

        if classifier is not None:
            return classifier

        raise WorkflowValidationError(
            f"Node '{node_name}' requires a classifier. Pass classifier=... or "
            "classifier_resolver={...} to WorkflowCompiler.compile()."
        )

    # ------------------------------------------------------------------
    # Edge wiring
    # ------------------------------------------------------------------

    def _wire_edge(
        self,
        *,
        graph: StateGraph[AgentState],
        edge: EdgeConfig,
        condition_resolver: ConditionResolver | None,
    ) -> None:
        source = START if edge.from_node.upper() == "START" else edge.from_node

        if edge.condition is None:
            if edge.to_node is None:  # Defensive; EdgeConfig already validates this.
                raise WorkflowValidationError(f"Edge from '{edge.from_node}' has no destination.")
            graph.add_edge(source, self._resolve_edge_target(edge.to_node))
            return

        condition = self._resolve_condition(edge.condition, condition_resolver)
        path_map: dict[Hashable, str] = {
            route_key: self._resolve_edge_target(target) for route_key, target in edge.targets.items()
        }
        graph.add_conditional_edges(source, condition, path_map)

    @staticmethod
    def _resolve_edge_target(target: str) -> str:
        if _is_terminal_target(target):
            return END
        return target

    def _resolve_condition(
        self,
        condition: str,
        resolver: ConditionResolver | None,
    ) -> Callable[..., str]:
        if resolver is None:
            return _default_condition_resolver(condition)

        if isinstance(resolver, Mapping):
            if condition in resolver:
                resolved = resolver[condition]
            else:
                folded = {str(key).casefold(): value for key, value in resolver.items()}
                resolved = folded.get(condition.casefold())
            if resolved is None:
                raise WorkflowValidationError(
                    f"Unknown edge condition '{condition}'. "
                    f"Available resolver conditions: {sorted(map(str, resolver))}."
                )
            if not callable(resolved):
                raise WorkflowValidationError(
                    f"Edge condition '{condition}' resolved to {type(resolved).__name__}, expected a callable router."
                )
            return cast(Callable[..., str], resolved)

        if callable(resolver):
            resolved = _invoke_with_optional_args(resolver, condition)
            if not callable(resolved):
                raise WorkflowValidationError(
                    f"Condition resolver returned {type(resolved).__name__} for '{condition}', "
                    "expected a callable router."
                )
            return cast(Callable[..., str], resolved)

        raise WorkflowValidationError(f"Unsupported condition_resolver type: {type(resolver).__name__}.")

    def _build_handoff_router(
        self,
        *,
        node_name: str,
        handoffs: list[str],
        fallback_target: str,
    ) -> tuple[dict[Hashable, str], Callable[[dict[str, Any]], str]]:
        path_map: dict[Hashable, str] = {}
        terminal_keys: dict[str, str] = {}

        for target in handoffs:
            if _is_terminal_target(target):
                normalized = _normalize_terminal(target)
                path_map[normalized] = END
                terminal_keys[normalized] = normalized
            else:
                path_map[target] = target

        fallback_key = f"__handoff_fallback__{node_name}"
        while fallback_key in path_map:
            fallback_key = f"{fallback_key}_"
        path_map[fallback_key] = fallback_target

        def _router(state: dict[str, Any]) -> str:
            active_agent = state.get("active_agent")
            if isinstance(active_agent, str):
                target = active_agent.strip()
                if target in path_map:
                    return target
                normalized = _normalize_terminal(target)
                if normalized in terminal_keys:
                    return terminal_keys[normalized]
            return fallback_key

        return path_map, _router

    # ------------------------------------------------------------------
    # Workflow metadata
    # ------------------------------------------------------------------

    def _build_workflow_info(
        self,
        manifest: WorkflowManifest,
        step_types: dict[str, type[WorkflowStep]],
    ) -> WorkflowInfo:
        generation_nodes = set(manifest.generation_nodes)
        if manifest.supports_streaming and not generation_nodes:
            for node_name, step_type in step_types.items():
                if issubclass(step_type, (AgentStep, PromptGenerationStep)):
                    generation_nodes.add(node_name)

        return WorkflowInfo(
            id=manifest.id,
            name=manifest.name,
            description=manifest.description,
            is_conversational=manifest.is_conversational,
            supports_streaming=manifest.supports_streaming,
            step_labels=dict(manifest.step_labels),
            generation_nodes=generation_nodes,
            metadata=dict(manifest.metadata),
        )


__all__ = [
    "CompiledDeclarativeWorkflow",
    "EdgeConfig",
    "StepConfig",
    "WorkflowCompiler",
    "WorkflowManifest",
    "WorkflowValidationError",
    "TERMINAL_TARGETS",
]
