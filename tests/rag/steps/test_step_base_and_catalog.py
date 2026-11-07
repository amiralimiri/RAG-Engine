"""Unit tests for WorkflowStep protocol, BaseStep mapping helpers, and StepCatalog."""

import threading
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from app.rag.steps.base import (
    BaseStep,
    StepCatalog,
    WorkflowStep,
    default_step_catalog,
    get_step_class,
    register_step,
    resolve_step_type,
)


class DummyConformingStep(BaseStep):
    """Test step inheriting BaseStep."""

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return {"result": "executed", "config_passed": config is not None}


class IndependentConformingStep:
    """Independent step implementing WorkflowStep protocol without BaseStep inheritance."""

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return {"status": "ok"}

    async def __call__(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await self.execute(state, config)


class IncompleteStepMissingCall:
    """Class missing __call__."""

    async def execute(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return {}


class IncompleteStepMissingExecute:
    """Class missing execute."""

    async def __call__(
        self, state: dict[str, Any], config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return {}


class TestWorkflowStepProtocol:
    """Tests verifying runtime protocol conformance of WorkflowStep."""

    def test_protocol_conformance_with_base_step_subclass(self) -> None:
        assert issubclass(DummyConformingStep, WorkflowStep)
        step = DummyConformingStep()
        assert isinstance(step, WorkflowStep)

    def test_protocol_conformance_with_independent_class(self) -> None:
        assert issubclass(IndependentConformingStep, WorkflowStep)
        step = IndependentConformingStep()
        assert isinstance(step, WorkflowStep)

    def test_protocol_rejection_for_incomplete_implementations(self) -> None:
        assert not issubclass(IncompleteStepMissingCall, WorkflowStep)
        assert not isinstance(IncompleteStepMissingCall(), WorkflowStep)

        assert not issubclass(IncompleteStepMissingExecute, WorkflowStep)
        assert not isinstance(IncompleteStepMissingExecute(), WorkflowStep)

    def test_base_step_cannot_be_instantiated_directly(self) -> None:
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseStep()  # type: ignore[abstract]


class TestBaseStepCallAndResolution:
    """Tests verifying BaseStep __call__ delegation and dot-path navigation."""

    @pytest.mark.asyncio
    async def test_call_delegates_to_execute(self) -> None:
        step = DummyConformingStep()
        config: RunnableConfig = {"tags": ["test"]}
        state: dict[str, Any] = {"input": "test"}

        result = await step(state, config)
        assert result == {"result": "executed", "config_passed": True}

    def test_resolve_input_top_level_fallback(self) -> None:
        step = DummyConformingStep()
        state = {"question": "What is RAG?"}

        assert step.resolve_input(state, "question") == "What is RAG?"
        assert step.resolve_input(state, "nonexistent", default="fallback") == "fallback"

    def test_resolve_input_nested_dot_path_success(self) -> None:
        step = DummyConformingStep(inputs={"query": "context.agent.query"})
        state = {
            "context": {
                "agent": {
                    "query": "nested query string",
                }
            }
        }
        assert step.resolve_input(state, "query") == "nested query string"

    def test_resolve_input_nested_missing_leaf_key(self) -> None:
        step = DummyConformingStep(inputs={"query": "context.agent.query"})
        state = {"context": {"agent": {}}}

        # Must not raise KeyError, should return default value
        assert step.resolve_input(state, "query", default="default_query") == "default_query"

    def test_resolve_input_nested_missing_intermediate_key(self) -> None:
        step = DummyConformingStep(inputs={"query": "context.agent.query"})
        state = {"context": {}}

        assert step.resolve_input(state, "query", default="default_query") == "default_query"

    def test_resolve_input_intermediate_non_dict(self) -> None:
        step = DummyConformingStep(inputs={"query": "context.agent.query"})
        state = {"context": {"agent": "not_a_dict"}}

        assert step.resolve_input(state, "query", default="fallback") == "fallback"

        state_none = {"context": None}
        assert step.resolve_input(state_none, "query", default="fallback") == "fallback"

    def test_resolve_input_empty_or_invalid_state(self) -> None:
        step = DummyConformingStep()
        assert step.resolve_input({}, "key", default="def") == "def"
        assert step.resolve_input(None, "key", default="def") == "def"  # type: ignore[arg-type]

    def test_resolve_input_preserves_none_leaf_value(self) -> None:
        step = DummyConformingStep()
        state = {"context": {"agent": {"active": None}}}
        assert step.resolve_input(state, "context.agent.active", default="fallback") is None


class TestBaseStepApplyOutputs:
    """Tests verifying BaseStep apply_outputs support for top-level and nested paths."""

    def test_apply_outputs_top_level_fields(self) -> None:
        step = DummyConformingStep(outputs={"answer": "answer", "source": "source_used"})
        state: dict[str, Any] = {"question": "Hello"}
        step_outputs = {"answer": "Generated answer", "source": "kb"}

        delta = step.apply_outputs(state, step_outputs)

        assert delta == {"answer": "Generated answer", "source_used": "kb"}
        assert state["answer"] == "Generated answer"
        assert state["source_used"] == "kb"
        assert state["question"] == "Hello"

    def test_apply_outputs_nested_paths_creates_structures(self) -> None:
        step = DummyConformingStep(
            outputs={"evidence": "context.evidence", "agent_info": "context.agent.details"}
        )
        state: dict[str, Any] = {}
        step_outputs = {
            "evidence": ["doc1", "doc2"],
            "agent_info": {"model": "gpt-4o"},
        }

        delta = step.apply_outputs(state, step_outputs)

        assert delta["context"]["evidence"] == ["doc1", "doc2"]
        assert delta["context"]["agent"]["details"] == {"model": "gpt-4o"}
        assert state["context"]["evidence"] == ["doc1", "doc2"]
        assert state["context"]["agent"]["details"] == {"model": "gpt-4o"}

    def test_apply_outputs_preserves_existing_nested_data(self) -> None:
        step = DummyConformingStep(outputs={"evidence": "context.evidence"})
        state: dict[str, Any] = {
            "context": {
                "user_id": "u-123",
                "prior_step": "init",
            }
        }
        step_outputs = {"evidence": ["doc-a"]}

        delta = step.apply_outputs(state, step_outputs)

        assert delta["context"]["user_id"] == "u-123"
        assert delta["context"]["prior_step"] == "init"
        assert delta["context"]["evidence"] == ["doc-a"]

        assert state["context"]["user_id"] == "u-123"
        assert state["context"]["evidence"] == ["doc-a"]

    def test_apply_outputs_deeply_nested_paths(self) -> None:
        step = DummyConformingStep(outputs={"result": "a.b.c.d"})
        state: dict[str, Any] = {"a": {"b": {"existing": 10}}}

        delta = step.apply_outputs(state, {"result": "deep_val"})

        assert delta["a"]["b"]["existing"] == 10
        assert delta["a"]["b"]["c"]["d"] == "deep_val"
        assert state["a"]["b"]["c"]["d"] == "deep_val"

    def test_apply_outputs_single_argument_mode(self) -> None:
        step = DummyConformingStep(outputs={"res": "context.val"})
        delta = step.apply_outputs({"res": "single_arg"})
        assert delta == {"context": {"val": "single_arg"}}


class TestStepCatalog:
    """Tests verifying thread-safety, registration, lookups, and dynamic imports."""

    @pytest.fixture(autouse=True)
    def clean_catalog(self) -> Any:
        default_step_catalog.clear()
        yield
        default_step_catalog.clear()

    def test_register_and_get_step_class(self) -> None:
        catalog = StepCatalog()
        catalog.register("dummy", DummyConformingStep)

        retrieved = catalog.get_step_class("dummy")
        assert retrieved is DummyConformingStep
        assert catalog.list_steps() == ["dummy"]

    def test_register_duplicate_raises_error_unless_override(self) -> None:
        catalog = StepCatalog()
        catalog.register("dummy", DummyConformingStep)

        with pytest.raises(ValueError, match="already registered"):
            catalog.register("dummy", DummyConformingStep)

        catalog.register("dummy", DummyConformingStep, override=True)
        assert catalog.get_step_class("dummy") is DummyConformingStep

    def test_register_invalid_step_type(self) -> None:
        catalog = StepCatalog()

        with pytest.raises(TypeError, match="Registered step must be a class"):
            catalog.register("invalid", DummyConformingStep())  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="does not implement the WorkflowStep protocol"):
            catalog.register("incomplete", IncompleteStepMissingCall)  # type: ignore[arg-type]

    def test_register_invalid_alias(self) -> None:
        catalog = StepCatalog()
        with pytest.raises(ValueError, match="non-empty string"):
            catalog.register("", DummyConformingStep)
        with pytest.raises(ValueError, match="non-empty string"):
            catalog.register("   ", DummyConformingStep)

    def test_unregister_and_clear(self) -> None:
        catalog = StepCatalog()
        catalog.register("s1", DummyConformingStep)
        catalog.register("s2", DummyConformingStep)

        assert catalog.unregister("s1") is True
        assert catalog.unregister("s1") is False
        assert catalog.list_steps() == ["s2"]

        catalog.clear()
        assert catalog.list_steps() == []

    def test_thread_safe_concurrent_registrations(self) -> None:
        catalog = StepCatalog()
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                catalog.register(f"step_{idx}", DummyConformingStep)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(catalog.list_steps()) == 25

    def test_register_step_decorator(self) -> None:
        catalog = StepCatalog()

        @register_step("decorated_step", catalog=catalog)
        class DecoratedStep(BaseStep):
            async def execute(
                self, state: dict[str, Any], config: RunnableConfig | None = None
            ) -> dict[str, Any]:
                return {"decorated": True}

        assert catalog.get_step_class("decorated_step") is DecoratedStep

    def test_resolve_step_type_alias_lookup(self) -> None:
        catalog = StepCatalog()
        catalog.register("retrieval", DummyConformingStep)

        assert catalog.resolve_step_type("retrieval") is DummyConformingStep

    def test_resolve_step_type_dynamic_colon_syntax(self) -> None:
        catalog = StepCatalog()
        # Resolve DummyConformingStep via dotted import: "tests.rag.steps.test_step_base_and_catalog:DummyConformingStep"
        identifier = f"{__name__}:DummyConformingStep"
        resolved = catalog.resolve_step_type(identifier)
        assert resolved is DummyConformingStep

    def test_resolve_step_type_dynamic_dot_syntax(self) -> None:
        catalog = StepCatalog()
        # Resolve BaseStep via dotted module path
        identifier = "app.rag.steps.base.BaseStep"
        resolved = catalog.resolve_step_type(identifier)
        assert resolved is BaseStep

    def test_resolve_step_type_nonexistent_alias_raises_key_error(self) -> None:
        catalog = StepCatalog()
        with pytest.raises(KeyError, match="is not registered in StepCatalog"):
            catalog.resolve_step_type("unknown_alias")

    def test_resolve_step_type_invalid_import_module_raises_import_error(self) -> None:
        catalog = StepCatalog()
        with pytest.raises(ImportError, match="Could not import module"):
            catalog.resolve_step_type("nonexistent.module:SomeStep")

    def test_resolve_step_type_missing_class_raises_attribute_error(self) -> None:
        catalog = StepCatalog()
        with pytest.raises(AttributeError, match="has no class or attribute 'NonexistentStep'"):
            catalog.resolve_step_type(f"{__name__}:NonexistentStep")

    def test_resolve_step_type_non_workflow_step_raises_type_error(self) -> None:
        catalog = StepCatalog()
        with pytest.raises(TypeError, match="must be a class implementing WorkflowStep protocol"):
            catalog.resolve_step_type(f"{__name__}:IncompleteStepMissingCall")

    def test_convenience_functions_use_default_catalog(self) -> None:
        @register_step("global_dummy")
        class GlobalDummyStep(BaseStep):
            async def execute(
                self, state: dict[str, Any], config: RunnableConfig | None = None
            ) -> dict[str, Any]:
                return {}

        assert get_step_class("global_dummy") is GlobalDummyStep
        assert resolve_step_type("global_dummy") is GlobalDummyStep
