"""Unit and integration tests for the declarative YAML workflow compiler."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from app.rag.state import Citation
from app.rag.steps import DEFAULT_STEP_CLASSES, BaseStep, StepCatalog
from app.rag.tools import FakeRAGTool, ToolEvidenceArtifact
from app.rag.workflows.base import WorkflowDefinition, WorkflowInput
from app.rag.workflows.compiler import (
    CompiledDeclarativeWorkflow,
    EdgeConfig,
    StepConfig,
    WorkflowCompiler,
    WorkflowManifest,
    WorkflowValidationError,
)
from tests.rag.steps.fakes import FakeChatModel, FakePromptStore, FakeWorkflowClassifier


class EchoStep(BaseStep):
    """Return the inbound question as the workflow answer."""

    async def execute(
        self,
        state: dict[str, Any],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        return {"answer": state.get("question", ""), "trace": ["echo"]}


class GradeStep(BaseStep):
    """Write a deterministic evidence grade to the blackboard."""

    def __init__(self, grade: str = "weak", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.grade = grade

    async def execute(
        self,
        state: dict[str, Any],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        return {"kb_grade": self.grade, "trace": [f"grade={self.grade}"]}


class ConstantAnswerStep(BaseStep):
    """Emit a fixed answer for deterministic conditional branch assertions."""

    def __init__(self, answer: str = "answer", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.answer = answer

    async def execute(
        self,
        state: dict[str, Any],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        return {"answer": self.answer, "trace": [f"answer={self.answer}"]}


def _route_grade(state: dict[str, Any]) -> str:
    """Route on the kg grade written by GradeStep."""
    return str(state.get("kb_grade") or "weak")


def _custom_catalog() -> StepCatalog:
    catalog = StepCatalog()
    for alias, step_class in DEFAULT_STEP_CLASSES.items():
        catalog.register(alias, step_class)
    catalog.register("echo", EchoStep)
    catalog.register("grade", GradeStep)
    catalog.register("answer", ConstantAnswerStep)
    return catalog


def _ai_tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


# ---------------------------------------------------------------------------
# Manifest parsing and validation
# ---------------------------------------------------------------------------


def test_manifest_parses_complete_yaml_topology() -> None:
    yaml_text = """
id: sample_linear
name: Sample Linear Workflow
description: A three-node declarative workflow
is_conversational: true
supports_streaming: true
entrypoint_node: retrieve
generation_nodes: [generate]
step_labels:
  retrieve: Retrieving documents
  generate: Generating answer
nodes:
  retrieve:
    type: tool_retrieval
    tools: [kb_tool]
    outputs:
      documents: kb_docs
  generate:
    type: prompt_generate
    prompt: rag:generate_kb
    model: primary
edges:
  - from_node: retrieve
    to_node: generate
  - from_node: generate
    to_node: finish
"""

    manifest = WorkflowManifest.from_yaml(yaml_text)

    assert manifest.id == "sample_linear"
    assert manifest.name == "Sample Linear Workflow"
    assert manifest.entrypoint_node == "retrieve"
    assert manifest.generation_nodes == ["generate"]
    assert manifest.step_labels["generate"] == "Generating answer"
    assert manifest.nodes["retrieve"].tools == ["kb_tool"]
    assert manifest.nodes["retrieve"].outputs == {"documents": "kb_docs"}
    assert manifest.edges[0].from_node == "retrieve"
    assert manifest.edges[0].to_node == "generate"
    assert manifest.edges[1].to_node == "finish"


def test_manifest_parses_from_file(tmp_path: Any) -> None:
    manifest_path = tmp_path / "workflow.yaml"
    manifest_path.write_text(
        """
id: file_workflow
name: File Workflow
supports_streaming: false
nodes:
  only:
    type: echo
""",
        encoding="utf-8",
    )

    manifest = WorkflowManifest.from_file(manifest_path)

    assert manifest.id == "file_workflow"
    assert list(manifest.nodes) == ["only"]


def test_manifest_rejects_empty_nodes() -> None:
    with pytest.raises(ValidationError, match="at least one node"):
        WorkflowManifest(id="empty", name="Empty", nodes={})


def test_manifest_rejects_unknown_generation_and_label_nodes() -> None:
    with pytest.raises(ValidationError, match="generation_nodes references undeclared"):
        WorkflowManifest(
            id="bad_generation",
            name="Bad Generation",
            nodes={"a": StepConfig(type="echo")},
            generation_nodes=["missing"],
            supports_streaming=False,
        )

    with pytest.raises(ValidationError, match="step_labels references undeclared"):
        WorkflowManifest(
            id="bad_labels",
            name="Bad Labels",
            nodes={"a": StepConfig(type="echo")},
            step_labels={"missing": "Nope"},
            supports_streaming=False,
        )


def test_manifest_rejects_edge_referencing_undeclared_node() -> None:
    with pytest.raises(ValidationError, match="references undeclared node 'missing'"):
        WorkflowManifest(
            id="bad_edge",
            name="Bad Edge",
            nodes={"a": StepConfig(type="echo")},
            edges=[EdgeConfig(from_node="a", to_node="missing")],
            supports_streaming=False,
        )


def test_manifest_rejects_condition_target_referencing_undeclared_node() -> None:
    with pytest.raises(ValidationError, match="targets\\['good'\\] references undeclared node"):
        WorkflowManifest(
            id="bad_target",
            name="Bad Target",
            nodes={"a": StepConfig(type="echo")},
            edges=[
                EdgeConfig(
                    from_node="a",
                    condition="route_grade",
                    targets={"good": "missing"},
                )
            ],
            supports_streaming=False,
        )


def test_manifest_rejects_invalid_entrypoint_node() -> None:
    with pytest.raises(ValidationError, match="entrypoint_node 'ghost'"):
        WorkflowManifest(
            id="bad_entry",
            name="Bad Entry",
            entrypoint_node="ghost",
            nodes={"a": StepConfig(type="echo")},
            supports_streaming=False,
        )


def test_edge_requires_destination_or_condition() -> None:
    with pytest.raises(ValidationError, match="must declare either to_node or a condition"):
        EdgeConfig(from_node="a")


def test_conditional_edge_requires_targets_and_rejects_to_node() -> None:
    with pytest.raises(ValidationError, match="must declare a non-empty targets mapping"):
        EdgeConfig(from_node="a", condition="route_grade")

    with pytest.raises(ValidationError, match="cannot also declare to_node"):
        EdgeConfig(
            from_node="a",
            to_node="b",
            condition="route_grade",
            targets={"good": "b"},
        )


def test_step_config_rejects_invalid_dotted_paths() -> None:
    with pytest.raises(ValidationError, match="invalid dynamic import path"):
        StepConfig(type="package.module:")

    with pytest.raises(ValidationError, match="invalid dynamic import path"):
        StepConfig(type="package..module:Class")

    with pytest.raises(ValidationError, match="invalid dot path"):
        StepConfig(type="agent", inputs={"query": "context..query"})

    with pytest.raises(ValidationError, match="invalid dot path"):
        StepConfig(type="agent", outputs={"answer": "context.answer."})


def test_manifest_rejects_handoff_to_undeclared_node() -> None:
    with pytest.raises(ValidationError, match="handoff to undeclared node 'ghost'"):
        WorkflowManifest(
            id="bad_handoff",
            name="Bad Handoff",
            nodes={"agent_a": StepConfig(type="agent", handoffs=["ghost"])},
            supports_streaming=False,
        )


def test_manifest_rejects_duplicate_edge_declarations() -> None:
    duplicate = EdgeConfig(from_node="a", to_node="b")

    with pytest.raises(ValidationError, match="Duplicate edge"):
        WorkflowManifest(
            id="duplicate_edges",
            name="Duplicate Edges",
            nodes={"a": StepConfig(type="echo"), "b": StepConfig(type="echo")},
            edges=[duplicate, duplicate],
            supports_streaming=False,
        )


def test_manifest_rejects_invalid_yaml_document() -> None:
    with pytest.raises(WorkflowValidationError, match="Invalid workflow YAML"):
        WorkflowManifest.from_yaml("id: broken\nnodes: [unclosed")


# ---------------------------------------------------------------------------
# Compilation and dependency resolution
# ---------------------------------------------------------------------------


def test_compile_rejects_unknown_step_alias_before_graph_construction() -> None:
    manifest = WorkflowManifest(
        id="unknown_alias",
        name="Unknown Alias",
        nodes={"a": StepConfig(type="does_not_exist")},
        supports_streaming=False,
    )
    compiler = WorkflowCompiler(catalog=StepCatalog())

    with pytest.raises(WorkflowValidationError, match="does_not_exist"):
        compiler.compile(manifest)


def test_compile_resolves_dotted_python_step_paths() -> None:
    manifest = WorkflowManifest(
        id="dotted_step",
        name="Dotted Step",
        nodes={
            "agent": StepConfig(
                type="app.rag.steps.agent.AgentStep",
                model="fake",
                prompt="rag:agent",
            )
        },
        supports_streaming=False,
    )
    model = FakeChatModel(response_text="Dotted path works.")
    compiler = WorkflowCompiler(
        model_resolver={"fake": model},
        prompt_store=FakePromptStore(),
    )

    workflow = compiler.compile(manifest)

    assert isinstance(workflow, CompiledDeclarativeWorkflow)
    assert isinstance(workflow, WorkflowDefinition)


def test_compile_linear_workflow_and_validate_metadata() -> None:
    manifest = WorkflowManifest(
        id="linear_echo",
        name="Linear Echo",
        description="A linear declarative workflow",
        generation_nodes=["echo"],
        step_labels={"echo": "Echoing question"},
        nodes={"echo": StepConfig(type="echo")},
        supports_streaming=False,
    )
    compiler = WorkflowCompiler(catalog=_custom_catalog())

    workflow = compiler.compile(manifest)

    assert workflow.info.id == "linear_echo"
    assert workflow.info.generation_nodes == {"echo"}
    assert workflow.info.step_labels == {"echo": "Echoing question"}
    initial_state = workflow.to_initial_state(WorkflowInput(question="hello world"))
    final_state = workflow.get_compiled_graph().invoke(initial_state)
    assert final_state["answer"] == "hello world"
    assert final_state["trace"] == ["echo"]


@pytest.mark.asyncio
async def test_compile_yaml_string_executes_custom_alias_steps() -> None:
    yaml_text = """
id: yaml_echo
name: YAML Echo
description: Compiled from an inline YAML string
supports_streaming: false
nodes:
  echo:
    type: echo
"""
    compiler = WorkflowCompiler(catalog=_custom_catalog())

    workflow = compiler.compile_yaml(yaml_text)
    initial_state = workflow.to_initial_state(WorkflowInput(question="from yaml"))
    final_state = await workflow.get_compiled_graph().ainvoke(initial_state)

    assert final_state["answer"] == "from yaml"


def test_compile_yaml_file_path(tmp_path: Any) -> None:
    manifest_path = tmp_path / "echo.yaml"
    manifest_path.write_text(
        """
id: yaml_file_echo
name: YAML File Echo
supports_streaming: false
nodes:
  echo:
    type: echo
""",
        encoding="utf-8",
    )
    compiler = WorkflowCompiler(catalog=_custom_catalog())

    workflow = compiler.compile_yaml(manifest_path)

    assert workflow.info.id == "yaml_file_echo"
    assert workflow.get_compiled_graph().invoke(workflow.to_initial_state(WorkflowInput(question="file path")))[
        "answer"
    ] == ("file path")


def test_compile_wires_explicit_conditional_edges() -> None:
    yaml_text = """
id: conditional_branch
name: Conditional Branch
supports_streaming: false
nodes:
  route:
    type: grade
    parameters:
      grade: good
  good:
    type: answer
    parameters:
      answer: Good evidence
  weak:
    type: answer
    parameters:
      answer: Weak evidence
edges:
  - from_node: route
    condition: grade_router
    targets:
      good: good
      weak: weak
"""
    compiler = WorkflowCompiler(
        catalog=_custom_catalog(),
        condition_resolver={"grade_router": _route_grade},
    )

    workflow = compiler.compile_yaml(yaml_text)
    final_state = workflow.get_compiled_graph().invoke(workflow.to_initial_state(WorkflowInput(question="grade this")))

    assert final_state["answer"] == "Good evidence"
    assert final_state["trace"] == ["grade=good", "answer=Good evidence"]


def test_compile_uses_explicit_unconditional_edge_as_handoff_fallback() -> None:
    manifest = WorkflowManifest(
        id="handoff_fallback",
        name="Handoff Fallback",
        supports_streaming=False,
        nodes={
            "triage": StepConfig(
                type="agent",
                model="fake",
                prompt="rag:agent",
                handoffs=["specialist", "finish"],
            ),
            "specialist": StepConfig(type="answer", parameters={"answer": "fallback answer"}),
        },
        edges=[EdgeConfig(from_node="triage", to_node="specialist")],
    )
    model = FakeChatModel(response_text="No handoff needed.")
    compiler = WorkflowCompiler(
        catalog=_custom_catalog(),
        model_resolver={"fake": model},
        prompt_store=FakePromptStore(),
    )

    workflow = compiler.compile(manifest)
    final_state = workflow.get_compiled_graph().invoke(
        workflow.to_initial_state(WorkflowInput(question="simple question"))
    )

    assert final_state["answer"] == "fallback answer"
    assert final_state.get("active_agent") is None


def test_compiler_rejects_handoffs_combined_with_conditional_edges() -> None:
    manifest = WorkflowManifest(
        id="conflicting_routing",
        name="Conflicting Routing",
        supports_streaming=False,
        nodes={
            "triage": StepConfig(
                type="agent",
                model="fake",
                prompt="rag:agent",
                handoffs=["finish"],
            ),
            "other": StepConfig(type="echo"),
        },
        edges=[
            EdgeConfig(
                from_node="triage",
                condition="route_grade",
                targets={"other": "other"},
            )
        ],
    )
    compiler = WorkflowCompiler(
        catalog=_custom_catalog(),
        model_resolver={"fake": FakeChatModel()},
        prompt_store=FakePromptStore(),
    )

    with pytest.raises(WorkflowValidationError, match="declares both handoffs and an explicit conditional edge"):
        compiler.compile(manifest)


def test_workflow_info_infers_generation_nodes_for_agent_steps() -> None:
    manifest = WorkflowManifest(
        id="inferred_generation",
        name="Inferred Generation",
        supports_streaming=True,
        nodes={
            "triage": StepConfig(
                type="agent",
                model="fake",
                prompt="rag:agent",
                handoffs=["finish"],
            )
        },
    )
    compiler = WorkflowCompiler(
        model_resolver={"fake": FakeChatModel()},
        prompt_store=FakePromptStore(),
    )

    workflow = compiler.compile(manifest)

    assert workflow.info.generation_nodes == {"triage"}


# ---------------------------------------------------------------------------
# End-to-end multi-agent handoff execution
# ---------------------------------------------------------------------------

MULTI_AGENT_YAML = """
id: it_support_multi_agent
name: IT Support Multi-Agent
description: Triage agent handing off to a security specialist
is_conversational: true
supports_streaming: true
entrypoint_node: START
generation_nodes: [triage_agent, security_agent]
step_labels:
  triage_agent: Triaging request
  security_agent: Investigating security issue
nodes:
  triage_agent:
    type: agent
    prompt: rag:agent
    model: fake
    tools: [kb_tool]
    handoffs: [security_agent, finish]
  security_agent:
    type: agent
    prompt: rag:agent
    model: fake
    tools: [security_tool]
    handoffs: [finish]
"""


@pytest.mark.asyncio
async def test_multi_agent_manifest_autonomous_handoff_end_to_end() -> None:
    triage_model = FakeChatModel(
        response_messages=[
            _ai_tool_call(
                "transfer_to_security_agent",
                {"reason": "suspected credential compromise"},
                "call_handoff",
            ),
            AIMessage(
                content="Reset your password from the self-service portal and enable MFA.",
                tool_calls=[
                    {
                        "name": "transfer_to_finish",
                        "args": {"reason": "security guidance delivered"},
                        "id": "call_finish",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    kb_tool = FakeRAGTool(
        name="kb_tool",
        content="Use the company VPN profile.",
        artifact=ToolEvidenceArtifact(
            citations=[Citation(title="vpn.md", type="private_kb")],
            retrieved_contexts=["Use the company VPN profile."],
            source_used="vectorstore",
        ),
    )
    security_tool = FakeRAGTool(
        name="security_tool",
        content="Rotate credentials immediately.",
        artifact=ToolEvidenceArtifact(
            citations=[Citation(title="incident.md", type="private_kb")],
            retrieved_contexts=["Rotate credentials immediately."],
            source_used="vectorstore",
        ),
    )
    prompt_store = FakePromptStore()
    compiler = WorkflowCompiler(
        tool_resolver={"kb_tool": kb_tool, "security_tool": security_tool},
        model_resolver={"fake": triage_model},
        prompt_store=prompt_store,
        classifier=FakeWorkflowClassifier(),
    )

    workflow = compiler.compile_yaml(MULTI_AGENT_YAML)
    initial_state = workflow.to_initial_state(
        WorkflowInput(
            question="I think my account was compromised.",
            session_id="sess-security",
            trace_id="trace-security",
        )
    )
    final_state = await workflow.get_compiled_graph().ainvoke(initial_state, {"recursion_limit": 10})

    assert final_state["active_agent"] == "finish"
    assert final_state["answer"] == ("Reset your password from the self-service portal and enable MFA.")
    assert final_state["trace"] == [
        "Agent 'triage_agent' → transfer_to_security_agent: suspected credential compromise",
        "Agent 'security_agent' completed workflow via 'transfer_to_finish': security guidance delivered",
    ]

    messages = final_state["messages"]
    assert isinstance(messages[0], HumanMessage)
    assert isinstance(messages[1], AIMessage)
    assert isinstance(messages[2], ToolMessage)
    assert isinstance(messages[3], AIMessage)
    assert isinstance(messages[4], ToolMessage)
    assert messages[1].tool_calls[0]["name"] == "transfer_to_security_agent"
    assert messages[3].tool_calls[0]["name"] == "transfer_to_finish"

    assert workflow.info.id == "it_support_multi_agent"
    assert workflow.info.generation_nodes == {"triage_agent", "security_agent"}
    assert workflow.info.step_labels["security_agent"] == "Investigating security issue"

    output = workflow.to_workflow_output(final_state, trace_id="trace-security")
    assert output.workflow == "it_support_multi_agent"
    assert output.answer == "Reset your password from the self-service portal and enable MFA."
    assert output.trace_id == "trace-security"
    assert output.rewritten_query == "I think my account was compromised."

    eval_context = workflow.extract_eval_context(final_state)
    assert eval_context.source_used == "direct"
    assert any(
        "Transferring control to agent 'security_agent'" in context for context in eval_context.retrieved_contexts
    )

    # Both steps rendered the shared prompt and each bound only its own scoped tools.
    assert [call["name"] for call in prompt_store.render_calls] == ["rag:agent", "rag:agent"]
    assert [tool.name for tool in triage_model.bind_calls[0]] == [
        "kb_tool",
        "transfer_to_security_agent",
        "transfer_to_finish",
    ]
    assert [tool.name for tool in triage_model.bind_calls[1]] == [
        "security_tool",
        "transfer_to_finish",
    ]


@pytest.mark.asyncio
async def test_handoff_terminal_end_target_routes_to_end() -> None:
    manifest = WorkflowManifest(
        id="terminal_end",
        name="Terminal End",
        supports_streaming=False,
        nodes={
            "agent": StepConfig(
                type="agent",
                model="fake",
                prompt="rag:agent",
                handoffs=["end"],
            )
        },
    )
    model = FakeChatModel(
        response_messages=[
            AIMessage(
                content="All done.",
                tool_calls=[
                    {
                        "name": "transfer_to_end",
                        "args": {"reason": "complete"},
                        "id": "call_end",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    compiler = WorkflowCompiler(
        model_resolver={"fake": model},
        prompt_store=FakePromptStore(),
    )

    workflow = compiler.compile(manifest)
    final_state = await workflow.get_compiled_graph().ainvoke(
        workflow.to_initial_state(WorkflowInput(question="finish this")),
        {"recursion_limit": 5},
    )

    assert final_state["active_agent"] == "end"
    assert final_state["answer"] == "All done."
