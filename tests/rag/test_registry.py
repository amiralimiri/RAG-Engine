"""Unit tests for the central thread-safe WorkflowRegistry."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock
import pytest

from app.rag.workflows.base import WorkflowDefinition, WorkflowInfo
from app.rag.workflows.registry import (
    WorkflowRegistry,
    get_workflow_registry,
    register_workflow,
)


def _make_mock_workflow(wf_id: str, name: str = "") -> WorkflowDefinition:
    wf = MagicMock()
    wf.info = WorkflowInfo(
        id=wf_id,
        name=name or wf_id.title(),
        description=f"Description for {wf_id}",
    )
    wf.get_compiled_graph = MagicMock(return_value=f"graph_{wf_id}")
    return wf


def test_registry_register_and_get():
    reg = WorkflowRegistry()
    wf1 = _make_mock_workflow("wf1", "Workflow 1")
    wf2 = _make_mock_workflow("wf2", "Workflow 2")

    reg.register(wf1)
    reg.register(wf2)

    assert len(reg) == 2
    assert "wf1" in reg
    assert "wf2" in reg
    assert "wf3" not in reg

    assert reg.get("wf1") == wf1
    assert reg.get("wf2") == wf2


def test_registry_duplicate_rejection():
    reg = WorkflowRegistry()
    wf1 = _make_mock_workflow("wf1")
    wf1_alt = _make_mock_workflow("wf1", "Alternative")

    reg.register(wf1)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(wf1_alt)

    # Allowed with override=True
    reg.register(wf1_alt, override=True)
    assert reg.get("wf1") == wf1_alt


def test_registry_missing_lookup():
    reg = WorkflowRegistry()
    reg.register(_make_mock_workflow("alpha"))

    with pytest.raises(KeyError, match="not found in registry"):
        reg.get("beta")


def test_registry_invalid_workflow():
    reg = WorkflowRegistry()
    invalid_wf = MagicMock()
    invalid_wf.info = None

    with pytest.raises(ValueError, match="must have an 'info' attribute"):
        reg.register(invalid_wf)


def test_registry_list_ids_and_info():
    reg = WorkflowRegistry()
    reg.register(_make_mock_workflow("b_workflow", "B"))
    reg.register(_make_mock_workflow("a_workflow", "A"))

    # Should be sorted by ID
    assert reg.list_ids() == ["a_workflow", "b_workflow"]
    info_list = reg.list_info()
    assert len(info_list) == 2
    assert info_list[0].id == "a_workflow"
    assert info_list[1].id == "b_workflow"


def test_registry_warmup():
    reg = WorkflowRegistry()
    wf1 = _make_mock_workflow("wf1")
    wf2 = _make_mock_workflow("wf2")
    reg.register(wf1)
    reg.register(wf2)

    reg.warmup()
    wf1.get_compiled_graph.assert_called_once()
    wf2.get_compiled_graph.assert_called_once()


def test_register_decorator_and_global_singleton():
    from app.rag.workflows import register_default_workflows
    global_reg = get_workflow_registry()

    try:
        # Direct function registration
        wf_inst = _make_mock_workflow("global_wf1")
        register_workflow(wf_inst)
        assert "global_wf1" in global_reg

        # Decorator on class
        @register_workflow
        class DecoratedWorkflow:
            def __init__(self):
                self.info = WorkflowInfo(
                    id="decorated_wf",
                    name="Decorated Workflow",
                    description="Class decorated workflow",
                )
                self.get_compiled_graph = MagicMock()

        assert "decorated_wf" in global_reg
        assert global_reg.get("decorated_wf").info.name == "Decorated Workflow"
    finally:
        # Clean up added test workflows and restore defaults
        register_default_workflows()


def test_thread_safety_concurrency():
    reg = WorkflowRegistry()

    def _worker(worker_id: int):
        wf_id = f"worker_{worker_id}"
        wf = _make_mock_workflow(wf_id)
        reg.register(wf)
        retrieved = reg.get(wf_id)
        assert retrieved.info.id == wf_id
        return wf_id

    workers_count = 20
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker, i) for i in range(workers_count)]
        results = [f.result() for f in futures]

    assert len(results) == workers_count
    assert len(reg) == workers_count
    assert reg.list_ids() == sorted([f"worker_{i}" for i in range(workers_count)])
