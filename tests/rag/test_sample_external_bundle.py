"""End-to-end integration tests for Sample External Workflow Bundle (sample_studio_agent)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.rag.workflows.base import (
    PluggableWorkflow,
    WorkflowDefinition,
    WorkflowInput,
    WorkflowOutput,
)
from app.rag.workflows.registry import WorkflowRegistry
from app.rag.workflows.runner import WorkflowRunner
from tests.rag.test_workflow_contracts import BaseWorkflowContractTest


def _parse_sse_frames(frames: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """Parse raw SSE string chunks into (event_type, payload_dict) tuples."""
    parsed: list[tuple[str, dict[str, Any]]] = []
    for frame in frames:
        event_type = ""
        data_payload: dict[str, Any] = {}
        for line in frame.strip().split("\n"):
            line = line.strip()
            if line.startswith("event: "):
                event_type = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                data_str = line[len("data: ") :].strip()
                data_payload = json.loads(data_str)
        if event_type:
            parsed.append((event_type, data_payload))
    return parsed


@pytest.fixture(scope="module")
def mounted_registry() -> WorkflowRegistry:
    """Mount workflows from local workflows/ directory."""
    registry = WorkflowRegistry()
    loaded_count = registry.load_from_directory("workflows/")
    assert loaded_count >= 1, "Expected at least 1 workflow bundle to be loaded from workflows/"
    return registry


@pytest.fixture(scope="module")
def sample_studio_workflow(mounted_registry: WorkflowRegistry) -> WorkflowDefinition:
    """Fixture resolving the sample_studio_agent workflow."""
    return mounted_registry.get("sample_studio_agent")


class TestSampleStudioAgentConformance(BaseWorkflowContractTest):
    """Verify sample_studio_agent satisfies all standard architectural workflow contracts."""

    __test__ = True

    @pytest.fixture
    def workflow(self, sample_studio_workflow: WorkflowDefinition) -> WorkflowDefinition:
        return sample_studio_workflow


def test_sample_studio_agent_registration(mounted_registry: WorkflowRegistry):
    """Assert sample_studio_agent is registered and fully compliant with manifest declarations."""
    assert "sample_studio_agent" in mounted_registry
    wf = mounted_registry.get("sample_studio_agent")

    assert isinstance(wf, PluggableWorkflow)
    assert wf.info.id == "sample_studio_agent"
    assert wf.info.name == "Sample Studio Research Agent"
    assert wf.info.is_conversational is True
    assert wf.info.supports_streaming is True
    assert wf.info.generation_nodes == {"generate_response"}

    expected_steps = {
        "analyze_intent": "Analyzing inquiry intent...",
        "mock_search": "Querying domain knowledge base...",
        "generate_response": "Synthesizing final findings...",
    }
    assert wf.info.step_labels == expected_steps

    # Verify registered profiles
    concise_profile = mounted_registry.get_profile("sample_studio_agent", "studio-concise")
    assert concise_profile is not None
    assert concise_profile.variant_id == "studio-concise"
    assert concise_profile.model_name == "mock-studio-concise"
    assert concise_profile.prompt_version == "concise"
    assert concise_profile.parameters.get("top_k") == 2

    default_profile = mounted_registry.get_profile("sample_studio_agent", "studio-default")
    assert default_profile is not None
    assert default_profile.variant_id == "studio-default"
    assert default_profile.prompt_version == "default"


def test_sample_studio_agent_execution_with_profile(mounted_registry: WorkflowRegistry):
    """Execute sample_studio_agent with variant_id='studio-concise' and verify output contracts."""
    runner = WorkflowRunner(registry=mounted_registry)
    query = "What are the latest breakthroughs in topological quantum computing?"

    request = WorkflowInput(
        question=query,
        workflow="sample_studio_agent",
        variant_id="studio-concise",
        session_id="session-studio-123",
        trace_id="trace-studio-test-001",
    )

    output = runner.execute(request)

    assert isinstance(output, WorkflowOutput)
    assert output.workflow == "sample_studio_agent"
    assert output.variant_id == "studio-concise"
    assert output.trace_id == "tracestudiotest001"

    # Verify model and prompt injection into synthesized response
    assert "[mock-studio-concise:concise]" in output.answer
    assert query in output.answer

    # Verify source_used, citations, and trace resolution
    assert output.source_used == "knowledge_base"
    assert len(output.citations) == 2
    assert output.citations[0]["title"] == "Studio KB Document 1"
    assert output.citations[1]["title"] == "Studio KB Document 2"

    assert len(output.trace) == 3
    assert f"Intent classified for query: '{query}'" in output.trace
    assert "Retrieved 2 contexts from knowledge base" in output.trace
    assert "Response synthesized successfully" in output.trace


def test_sample_studio_agent_execution_with_context(mounted_registry: WorkflowRegistry):
    """Execute via execute_with_context and verify EvalContext extraction."""
    runner = WorkflowRunner(registry=mounted_registry)
    request = WorkflowInput(
        question="Explain topological qubits.",
        workflow="sample_studio_agent",
        variant_id="studio-concise",
    )

    result = runner.execute_with_context(request)
    assert result.response.workflow == "sample_studio_agent"
    assert result.eval_context.source_used == "knowledge_base"
    assert len(result.eval_context.retrieved_contexts) == 2
    assert "Context chunk #1" in result.eval_context.retrieved_contexts[0]
    assert "Context chunk #2" in result.eval_context.retrieved_contexts[1]


@pytest.mark.asyncio
async def test_sample_studio_agent_streaming_pipeline(mounted_registry: WorkflowRegistry):
    """Verify streaming emits step transitions, token chunks, and terminal metadata cleanly."""
    runner = WorkflowRunner(registry=mounted_registry)
    query = "How do Majorana zero modes enable fault tolerance?"

    request = WorkflowInput(
        question=query,
        workflow="sample_studio_agent",
        variant_id="studio-concise",
        session_id="stream-studio-sess",
        trace_id="stream-studio-trace-789",
    )

    raw_frames = [frame async for frame in runner.stream(request)]
    assert len(raw_frames) > 0, "Stream must emit frames"

    parsed_events = _parse_sse_frames(raw_frames)
    event_types = [event_type for event_type, _ in parsed_events]

    # Verify all expected event types exist
    assert "step" in event_types
    assert "token" in event_types
    assert "metadata" in event_types

    # Verify step progression
    step_events = [data for event_type, data in parsed_events if event_type == "step"]
    step_names = [s["step"] for s in step_events]
    assert step_names == ["analyze_intent", "mock_search", "generate_response"]

    assert step_events[0]["label"] == "Analyzing inquiry intent..."
    assert step_events[1]["label"] == "Querying domain knowledge base..."
    assert step_events[2]["label"] == "Synthesizing final findings..."

    # Verify token deltas
    token_events = [data for event_type, data in parsed_events if event_type == "token"]
    assert len(token_events) > 0
    streamed_answer = "".join(t["delta"] for t in token_events)
    assert "[mock-studio-concise:concise]" in streamed_answer
    assert query in streamed_answer

    # Verify terminal metadata event
    metadata_events = [data for event_type, data in parsed_events if event_type == "metadata"]
    assert len(metadata_events) == 1
    meta = metadata_events[0]
    assert meta["workflow"] == "sample_studio_agent"
    assert meta["variant_id"] == "studio-concise"
    assert meta["source_used"] == "knowledge_base"
    assert len(meta["citations"]) == 2
    assert meta["rewritten_query"] == query
    assert len(meta["trace"]) == 3
