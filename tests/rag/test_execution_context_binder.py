"""Exhaustive unit tests for ExecutionContextBinder pre-execution seam."""

import pytest
from fastapi import HTTPException

from app.rag.workflows.base import PluggableWorkflow, WorkflowInfo, WorkflowInput
from app.rag.workflows.binder import ExecutionContextBinder
from app.rag.workflows.registry import WorkflowRegistry


class StubWorkflow(PluggableWorkflow):
    """Minimal in-memory workflow double without LangGraph dependencies."""

    def __init__(self, workflow_id: str = "stub_wf", metadata: dict | None = None) -> None:
        info = WorkflowInfo(
            id=workflow_id,
            name="Stub Workflow",
            description="Stub for binder tests",
            metadata=metadata or {},
            generation_nodes={"generate"},
        )
        super().__init__(info=info, graph=object())


def test_resolve_workflow_success():
    """Verify resolve_workflow returns registered workflow definition."""
    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="test_wf")
    registry.register(stub)

    binder = ExecutionContextBinder(registry=registry)
    wf_def = binder.resolve_workflow("test_wf")

    assert wf_def.info.id == "test_wf"


def test_resolve_workflow_auto_registers_defaults_when_empty():
    """Verify resolve_workflow populates defaults when registry is empty."""
    empty_registry = WorkflowRegistry()
    assert len(empty_registry) == 0

    binder = ExecutionContextBinder(registry=empty_registry)
    wf_def = binder.resolve_workflow("fix_single_llm")

    assert wf_def.info.id == "fix_single_llm"
    assert len(empty_registry) > 0


def test_resolve_workflow_unknown_raises_http_400():
    """Verify resolve_workflow raises HTTPException(400) for missing workflow."""
    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="registered_wf")
    registry.register(stub)

    binder = ExecutionContextBinder(registry=registry)
    with pytest.raises(HTTPException) as exc_info:
        binder.resolve_workflow("non_existent_wf")

    assert exc_info.value.status_code == 400
    assert "Unknown workflow: non_existent_wf" in exc_info.value.detail


def test_parameter_precedence_tier3_baseline_only():
    """Verify tier 3 workflow baseline parameters are applied when no overrides exist."""
    registry = WorkflowRegistry()
    stub = StubWorkflow(
        workflow_id="baseline_wf",
        metadata={
            "parameters": {"temperature": 0.2, "top_p": 0.9},
            "model_name": "gpt-4o-mini",
        },
    )
    registry.register(stub)

    binder = ExecutionContextBinder(registry=registry)
    req = WorkflowInput(question="What is RAG?", workflow="baseline_wf")

    bound = binder.bind(req)

    assert bound.resolved_parameters["temperature"] == 0.2
    assert bound.resolved_parameters["top_p"] == 0.9
    assert bound.resolved_parameters["model_name"] == "gpt-4o-mini"
    assert bound.as_runnable_config()["configurable"]["temperature"] == 0.2
    assert bound.as_runnable_config()["configurable"]["model_name"] == "gpt-4o-mini"


def test_parameter_precedence_tier2_profile_overrides_baseline():
    """Verify tier 2 profile parameters override tier 3 baseline metadata."""
    from app.rag.workflows.base import ExecutionProfile

    registry = WorkflowRegistry()
    stub = StubWorkflow(
        workflow_id="profile_wf",
        metadata={
            "parameters": {"temperature": 0.2, "base_key": "from_baseline"},
            "model_name": "baseline-model",
        },
    )
    registry.register(stub)

    profile = ExecutionProfile(
        variant_id="fast_profile",
        model_name="profile-model",
        temperature=0.5,
        parameters={"profile_key": "from_profile"},
    )
    registry.register_profile("profile_wf", profile)

    binder = ExecutionContextBinder(registry=registry)
    req = WorkflowInput(question="Query", workflow="profile_wf", variant_id="fast_profile")

    bound = binder.bind(req)

    assert bound.resolved_parameters["temperature"] == 0.5
    assert bound.resolved_parameters["model_name"] == "profile-model"
    assert bound.resolved_parameters["base_key"] == "from_baseline"
    assert bound.resolved_parameters["profile_key"] == "from_profile"
    assert bound.variant_id == "fast_profile"


def test_parameter_precedence_tier1_explicit_overrides_profile_and_baseline():
    """Verify tier 1 explicit overrides take precedence over both profile and baseline."""
    from app.rag.workflows.base import ExecutionProfile

    registry = WorkflowRegistry()
    stub = StubWorkflow(
        workflow_id="override_wf",
        metadata={
            "parameters": {"temperature": 0.2, "prompt_version": "v1"},
            "model_name": "baseline-model",
        },
    )
    registry.register(stub)

    profile = ExecutionProfile(
        variant_id="prod_profile",
        model_name="profile-model",
        temperature=0.7,
        prompt_version="v2",
        parameters={"batch_size": 16},
    )
    registry.register_profile("override_wf", profile)

    binder = ExecutionContextBinder(registry=registry)
    req = WorkflowInput(
        question="Query",
        workflow="override_wf",
        variant_id="prod_profile",
        parameters={"temperature": 0.99, "batch_size": 32},
    )

    bound = binder.bind(req, model_name="explicit-override-model", prompt_version="v3")

    # Explicit Tier 1 overrides win
    assert bound.resolved_parameters["temperature"] == 0.99
    assert bound.resolved_parameters["batch_size"] == 32
    assert bound.resolved_parameters["model_name"] == "explicit-override-model"
    assert bound.resolved_parameters["prompt_version"] == "v3"


def test_telemetry_tags_compilation_and_deduplication():
    """Verify multi-dimensional tags are compiled and deduplicated preserving order."""
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage, HumanMessage

    from app.rag.workflows.base import ExecutionProfile

    registry = WorkflowRegistry()
    stub = StubWorkflow(
        workflow_id="telemetry_wf",
        metadata={
            "tags": ["tag-base", "tag-shared"],
            "model_name": "base-model",
            "prompt_version": "v1.0",
        },
    )
    registry.register(stub)

    profile = ExecutionProfile(
        variant_id="tel_profile",
        model_name="prof-model",
        tags=["tag-prof", "tag-shared"],
    )
    registry.register_profile("telemetry_wf", profile)

    captured_cb_args = {}

    def mock_cb_factory(**kwargs):
        captured_cb_args.update(kwargs)
        return MagicMock(last_trace_id="cb-trace-id")

    mock_settings = MagicMock()
    mock_settings.app_env = "staging"

    binder = ExecutionContextBinder(
        registry=registry,
        callback_factory=mock_cb_factory,
        settings=mock_settings,
    )

    class CustomRequest(WorkflowInput):
        tags: list[str] = []

    req = CustomRequest(
        question="Query",
        workflow="telemetry_wf",
        variant_id="tel_profile",
        tags=["tag-req", "tag-shared"],
        messages=[HumanMessage(content="Hi"), AIMessage(content="Hello")],
    )

    bound = binder.bind(
        req,
        tags=["tag-kw"],
        config={"tags": ["tag-cfg"]},
    )

    tags = captured_cb_args.get("tags", [])
    assert "workflow:telemetry_wf" in tags
    assert "env:staging" in tags
    assert "variant:tel_profile" in tags
    assert "model:prof-model" in tags
    assert "prompt:v1.0" in tags
    assert "turn_count:1" in tags
    assert "tag-base" in tags
    assert "tag-prof" in tags
    assert "tag-req" in tags
    assert "tag-kw" in tags
    assert "tag-cfg" in tags
    assert tags.count("tag-shared") == 1


def test_telemetry_trace_id_sanitization_and_uuid_fallback():
    """Verify trace_id removes hyphens and generates a valid hex fallback when omitted."""
    import re

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="trace_wf")
    registry.register(stub)

    binder = ExecutionContextBinder(registry=registry)

    # 1. Hyphenated trace_id
    req_hyphen = WorkflowInput(
        question="Hi",
        workflow="trace_wf",
        trace_id="12345678-ABCD-ef01-2345-6789abcdef01",
    )
    bound_hyphen = binder.bind(req_hyphen)
    assert bound_hyphen.trace_id == "12345678abcdef0123456789abcdef01"
    assert "-" not in bound_hyphen.trace_id

    # 2. Omitted trace_id generates 32-char hex fallback
    req_none = WorkflowInput(question="Hi", workflow="trace_wf")
    bound_none = binder.bind(req_none)
    assert re.match(r"^[0-9a-f]{32}$", bound_none.trace_id)


def test_structlog_contextvars_binding():
    """Verify execution contextvars are bound with correlation keys."""
    import structlog

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="ctx_wf")
    registry.register(stub)

    binder = ExecutionContextBinder(registry=registry)
    req = WorkflowInput(
        question="Hi",
        workflow="ctx_wf",
        session_id="ses-12345",
        user_id="usr-99",
        variant_id="test-var",
    )

    bound = binder.bind(req)

    cv = structlog.contextvars.get_contextvars()
    assert cv.get("workflow") == "ctx_wf"
    assert cv.get("trace_id") == bound.trace_id
    assert cv.get("session_id") == "ses-12345"
    assert cv.get("user_id") == "usr-99"
    assert cv.get("variant_id") == "test-var"


@pytest.mark.asyncio
async def test_abind_hydrates_messages_directly_via_aget_messages():
    """Verify abind retrieves messages asynchronously using aget_messages."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import AIMessage, HumanMessage

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="mem_wf")
    registry.register(stub)

    fake_messages = [HumanMessage(content="Hello"), AIMessage(content="Hi there")]
    mock_memory = MagicMock()
    mock_memory.aget_messages = AsyncMock(return_value=fake_messages)
    mock_memory.aget_turns = AsyncMock()

    binder = ExecutionContextBinder(registry=registry, memory=mock_memory)
    req = WorkflowInput(question="Next query", workflow="mem_wf", session_id="ses-100")

    bound = await binder.abind(req)

    assert bound.hydrated_messages == fake_messages
    assert bound.session_id == "ses-100"
    mock_memory.aget_messages.assert_awaited_once_with(
        "ses-100",
        limit=10,
        before_turn_index=None,
    )
    mock_memory.aget_turns.assert_not_called()


def test_bind_hydrates_messages_synchronously():
    """Verify synchronous bind hydrates messages using runner threadpool/concurrency safe helper."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import AIMessage, HumanMessage

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="sync_mem_wf")
    registry.register(stub)

    fake_messages = [HumanMessage(content="Prior q"), AIMessage(content="Prior a")]
    mock_memory = MagicMock()
    mock_memory.aget_messages = AsyncMock(return_value=fake_messages)

    binder = ExecutionContextBinder(registry=registry, memory=mock_memory)
    req = WorkflowInput(question="Current q", workflow="sync_mem_wf", session_id="ses-sync")

    bound = binder.bind(req)

    assert bound.hydrated_messages == fake_messages
    assert bound.session_id == "ses-sync"


@pytest.mark.asyncio
async def test_rewind_slicing_with_before_turn_index():
    """Verify rewind_turn_index passes before_turn_index and isolates turns [:rewind_turn_index]."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import AIMessage, HumanMessage

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="rewind_wf")
    registry.register(stub)

    # 2 turns previously: turn 0 and turn 1
    # When rewind_turn_index=1, only turn 0 should be retrieved
    turn0_messages = [HumanMessage(content="Turn 0 q"), AIMessage(content="Turn 0 a")]

    mock_memory = MagicMock()
    mock_memory.aget_messages = AsyncMock(return_value=turn0_messages)

    binder = ExecutionContextBinder(registry=registry, memory=mock_memory)
    req = WorkflowInput(
        question="Rewound question",
        workflow="rewind_wf",
        session_id="ses-rewind",
        rewind_turn_index=1,
    )

    bound = await binder.abind(req)

    assert bound.hydrated_messages == turn0_messages
    mock_memory.aget_messages.assert_awaited_once_with(
        "ses-rewind",
        limit=10,
        before_turn_index=1,
    )


@pytest.mark.asyncio
async def test_rewind_slicing_with_aget_turns_fallback():
    """Verify message hydration fallback slices turns and converts to HumanMessage/AIMessage pairs."""
    from unittest.mock import AsyncMock

    from app.rag.memory.base import ConversationTurn

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="turns_wf")
    registry.register(stub)

    # Turn store providing only aget_turns
    class TurnsOnlyMemory:
        def __init__(self) -> None:
            self.aget_turns = AsyncMock(
                return_value=[
                    ConversationTurn(
                        session_id="ses-fallback",
                        turn_index=0,
                        user_query="Q0",
                        assistant_answer="A0",
                    ),
                    ConversationTurn(
                        session_id="ses-fallback",
                        turn_index=1,
                        user_query="Q1",
                        assistant_answer="A1",
                    ),
                ]
            )

    memory = TurnsOnlyMemory()
    binder = ExecutionContextBinder(registry=registry, memory=memory)  # type: ignore

    req = WorkflowInput(
        question="New Q",
        workflow="turns_wf",
        session_id="ses-fallback",
        rewind_turn_index=1,
    )

    bound = await binder.abind(req)

    # Should slice to only turn 0
    assert bound.hydrated_messages is not None
    assert len(bound.hydrated_messages) == 2
    assert bound.hydrated_messages[0].content == "Q0"
    assert bound.hydrated_messages[1].content == "A0"


@pytest.mark.asyncio
async def test_hydration_skipped_when_pre_supplied_or_no_session():
    """Verify memory queries are skipped when messages are already supplied or session_id is absent."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import HumanMessage

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="skip_wf")
    registry.register(stub)

    mock_memory = MagicMock()
    mock_memory.aget_messages = AsyncMock()
    mock_memory.aget_turns = AsyncMock()

    binder = ExecutionContextBinder(registry=registry, memory=mock_memory)

    # 1. No session_id
    req1 = WorkflowInput(question="No session", workflow="skip_wf")
    bound1 = await binder.abind(req1)
    assert bound1.hydrated_messages is None
    mock_memory.aget_messages.assert_not_called()

    # 2. Sentinel session_id
    req2 = WorkflowInput(question="Null session", workflow="skip_wf", session_id="undefined")
    bound2 = await binder.abind(req2)
    assert bound2.hydrated_messages is None
    mock_memory.aget_messages.assert_not_called()

    # 3. Pre-supplied hydrated_messages
    pre_supplied = [HumanMessage(content="Pre-supplied")]
    req3 = WorkflowInput(question="With session", workflow="skip_wf", session_id="ses-valid")
    bound3 = await binder.abind(req3, hydrated_messages=pre_supplied)
    assert bound3.hydrated_messages == pre_supplied
    mock_memory.aget_messages.assert_not_called()


def test_binder_properties_and_di():
    """Verify binder dependency injection properties and fallbacks."""
    binder_default = ExecutionContextBinder()

    # Default registry fallback
    assert binder_default.registry is not None
    assert binder_default.memory is None

    # Custom injected dependencies
    custom_settings = object()

    def custom_cb(**kwargs):
        return None

    def custom_extractor(h):
        return "custom-extracted"

    custom_registry = WorkflowRegistry()
    custom_memory = object()

    binder_custom = ExecutionContextBinder(
        registry=custom_registry,
        memory=custom_memory,  # type: ignore
        callback_factory=custom_cb,
        trace_extractor=custom_extractor,
        settings=custom_settings,
    )

    assert binder_custom.registry is custom_registry
    assert binder_custom.memory is custom_memory
    assert binder_custom.settings is custom_settings
    assert binder_custom.callback_factory is custom_cb
    assert binder_custom.trace_extractor is custom_extractor


def test_streaming_flag_and_custom_callbacks():
    """Verify streaming flag populates metadata and custom callback is preserved."""
    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="stream_wf")
    registry.register(stub)

    binder = ExecutionContextBinder(registry=registry)
    req = WorkflowInput(question="Stream query", workflow="stream_wf")

    custom_cb = object()
    bound = binder.bind(
        req,
        streaming=True,
        callback=custom_cb,
        config={"callbacks": ["existing_cb"], "tags": ["inbound-tag"]},
    )

    run_config = bound.as_runnable_config()
    assert run_config["metadata"]["streaming"] is True
    # If callbacks already present in config, preserved
    assert "existing_cb" in run_config["callbacks"]


def test_bound_execution_context_immutability():
    """Verify BoundExecutionContext is frozen and rejects mutations."""
    from pydantic import ValidationError

    registry = WorkflowRegistry()
    stub = StubWorkflow(workflow_id="immutable_wf")
    registry.register(stub)

    binder = ExecutionContextBinder(registry=registry)
    req = WorkflowInput(question="Immutable test", workflow="immutable_wf")
    bound = binder.bind(req)

    with pytest.raises((ValidationError, AttributeError)):
        bound.trace_id = "new_trace_id"  # type: ignore

    cfg = bound.as_runnable_config()
    cfg["new_key"] = "modified"
    assert "new_key" not in bound.config
