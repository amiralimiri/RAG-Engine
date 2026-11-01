"""Comprehensive unit and integration tests for dynamic manifest and factory discovery in WorkflowRegistry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from app.rag.workflows.base import (
    PluggableWorkflow,
    StandardWorkflowState,
    WorkflowInput,
    WorkflowOutput,
)
from app.rag.workflows.registry import (
    WorkflowRegistry,
    load_workflows_from_directory,
)


def _create_bundle(
    bundle_dir: Path,
    *,
    manifest_dict: dict[str, Any] | None = None,
    manifest_raw: str | None = None,
    code: str | None = None,
    filename: str = "workflow.py",
) -> Path:
    """Helper to create a dynamic workflow bundle directory with manifest and python code."""
    bundle_dir.mkdir(parents=True, exist_ok=True)

    if manifest_raw is not None:
        (bundle_dir / "manifest.json").write_text(manifest_raw, encoding="utf-8")
    elif manifest_dict is not None:
        (bundle_dir / "manifest.json").write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")

    if code is not None:
        (bundle_dir / filename).write_text(code, encoding="utf-8")

    return bundle_dir


def test_load_from_directory_nonexistent_path(tmp_path: Path):
    """Verify loading from nonexistent directory returns 0 and does not raise."""
    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path / "does_not_exist")
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_file_path(tmp_path: Path):
    """Verify passing a regular non-manifest file path returns 0 and does not raise."""
    dummy_file = tmp_path / "not_a_dir.txt"
    dummy_file.write_text("hello", encoding="utf-8")

    reg = WorkflowRegistry()
    count = reg.load_from_directory(dummy_file)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_empty_directory(tmp_path: Path):
    """Verify loading from an empty directory returns 0."""
    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_valid_single_bundle(tmp_path: Path):
    """Verify discovering and registering a valid single bundle in a subdirectory."""
    bundle_dir = tmp_path / "sample_search_rag"
    manifest = {
        "id": "sample_search_rag",
        "name": "Sample Search RAG",
        "description": "Pluggable search-based RAG workflow bundle",
        "entrypoint": "workflow:get_graph",
        "is_conversational": True,
        "supports_streaming": True,
        "step_labels": {"retrieve": "Retrieving documents", "generate": "Synthesizing answer"},
        "generation_nodes": ["generate"],
        "metadata": {"version": "1.0.0", "author": "Harness Team"},
    }
    code = """
from langgraph.graph import StateGraph, START, END
from app.rag.workflows.base import StandardWorkflowState

def retrieve_node(state: StandardWorkflowState):
    return {
        "retrieved_contexts": ["Doc excerpt A", "Doc excerpt B"],
        "source_used": "knowledge_base",
        "trace": ["Retrieved documents"],
    }

def generate_node(state: StandardWorkflowState):
    return {
        "answer": "Answer based on search.",
        "citations": [{"title": "Doc A", "url": "https://example.com/a"}],
        "trace": list(state.get("trace") or []) + ["Generated answer"],
    }

def get_graph(config=None):
    builder = StateGraph(StandardWorkflowState)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("generate", generate_node)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)
    return builder.compile()
"""
    _create_bundle(bundle_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)

    assert count == 1
    assert "sample_search_rag" in reg

    wf = reg.get("sample_search_rag")
    assert isinstance(wf, PluggableWorkflow)
    assert wf.info.id == "sample_search_rag"
    assert wf.info.name == "Sample Search RAG"
    assert wf.info.is_conversational is True
    assert wf.info.supports_streaming is True
    assert wf.info.generation_nodes == {"generate"}
    assert wf.info.step_labels == {"retrieve": "Retrieving documents", "generate": "Synthesizing answer"}
    assert wf.info.metadata == {"version": "1.0.0", "author": "Harness Team"}

    # Test compilation
    compiled = wf.get_compiled_graph()
    assert compiled is not None
    assert hasattr(compiled, "invoke") or hasattr(compiled, "ainvoke")


def test_load_from_directory_direct_bundle_path(tmp_path: Path):
    """Verify passing the bundle directory directly loads and registers the workflow."""
    bundle_dir = tmp_path / "direct_bundle"
    manifest = {
        "id": "direct_loaded_wf",
        "name": "Direct Loaded Workflow",
        "description": "Workflow loaded directly from bundle directory",
        "entrypoint": "workflow:get_graph",
        "supports_streaming": False,
        "generation_nodes": [],
    }
    code = """
def get_graph():
    return lambda state: {"answer": "direct output"}
"""
    _create_bundle(bundle_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(bundle_dir)

    assert count == 1
    assert "direct_loaded_wf" in reg
    assert reg.get("direct_loaded_wf").info.supports_streaming is False


def test_load_from_directory_multiple_valid_bundles(tmp_path: Path):
    """Verify discovering and registering multiple independent bundles."""
    for i in (1, 2, 3):
        b_dir = tmp_path / f"bundle_{i}"
        manifest = {
            "id": f"multi_wf_{i}",
            "name": f"Multi Workflow {i}",
            "description": f"Multi workflow test #{i}",
            "entrypoint": "workflow:get_graph",
            "supports_streaming": True,
            "generation_nodes": [f"node_{i}"],
        }
        code = f"""
def get_graph():
    return lambda state: {{"answer": "Answer from {i}"}}
"""
        _create_bundle(b_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)

    assert count == 3
    assert reg.list_ids() == ["multi_wf_1", "multi_wf_2", "multi_wf_3"]


def test_load_from_directory_invalid_json(tmp_path: Path):
    """Verify corrupted JSON manifest is logged and gracefully skipped."""
    b_dir = tmp_path / "bad_json_bundle"
    _create_bundle(
        b_dir,
        manifest_raw="""{ "id": "bad_json", "broken: [unclosed""",
        code="def get_graph(): return None",
    )

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_missing_entrypoint(tmp_path: Path):
    """Verify manifest missing entrypoint is logged and gracefully skipped."""
    b_dir = tmp_path / "no_entrypoint_bundle"
    manifest = {
        "id": "no_entrypoint_wf",
        "name": "No Entrypoint",
        "description": "Missing entrypoint field",
        "supports_streaming": False,
    }
    _create_bundle(b_dir, manifest_dict=manifest, code="def get_graph(): pass")

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_invalid_entrypoint_format(tmp_path: Path):
    """Verify entrypoint missing colon separator is logged and gracefully skipped."""
    b_dir = tmp_path / "bad_format_bundle"
    manifest = {
        "id": "bad_format_wf",
        "name": "Bad Entrypoint Format",
        "description": "Entrypoint without colon",
        "entrypoint": "invalid_entrypoint_no_colon",
        "supports_streaming": False,
    }
    _create_bundle(b_dir, manifest_dict=manifest, code="def get_graph(): pass")

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_syntax_error_in_module(tmp_path: Path):
    """Verify python module with syntax error is caught and skipped without crashing."""
    b_dir = tmp_path / "syntax_error_bundle"
    manifest = {
        "id": "syntax_err_wf",
        "name": "Syntax Error Workflow",
        "description": "Module contains a syntax error",
        "entrypoint": "workflow:get_graph",
        "supports_streaming": False,
    }
    code = """
def broken_syntax(
    this is invalid python code !@#$
"""
    _create_bundle(b_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_runtime_error_on_import(tmp_path: Path):
    """Verify python module throwing on import is caught and skipped without crashing."""
    b_dir = tmp_path / "runtime_err_bundle"
    manifest = {
        "id": "runtime_err_wf",
        "name": "Runtime Error Workflow",
        "description": "Module raises exception at top level",
        "entrypoint": "workflow:get_graph",
        "supports_streaming": False,
    }
    code = """
raise RuntimeError("Catastrophic top-level import error")
"""
    _create_bundle(b_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_missing_callable_attribute(tmp_path: Path):
    """Verify module missing the declared entrypoint callable is skipped."""
    b_dir = tmp_path / "missing_attr_bundle"
    manifest = {
        "id": "missing_attr_wf",
        "name": "Missing Attribute Workflow",
        "description": "Declared entrypoint function does not exist in module",
        "entrypoint": "workflow:non_existent_factory",
        "supports_streaming": False,
    }
    code = """
def other_factory():
    return None
"""
    _create_bundle(b_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_non_callable_attribute(tmp_path: Path):
    """Verify entrypoint pointing to a non-callable, non-runnable object is rejected."""
    b_dir = tmp_path / "non_callable_bundle"
    manifest = {
        "id": "non_callable_wf",
        "name": "Non Callable Workflow",
        "description": "Entrypoint points to a string constant",
        "entrypoint": "workflow:not_callable_attr",
        "supports_streaming": False,
    }
    code = """
not_callable_attr = "Just a string, not a callable or graph runnable"
"""
    _create_bundle(b_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_missing_python_file(tmp_path: Path):
    """Verify entrypoint pointing to non-existent module file is skipped."""
    b_dir = tmp_path / "missing_file_bundle"
    manifest = {
        "id": "missing_file_wf",
        "name": "Missing Module File Workflow",
        "description": "Manifest references python file that doesn't exist",
        "entrypoint": "nonexistent_module:get_graph",
        "supports_streaming": False,
    }
    _create_bundle(b_dir, manifest_dict=manifest)  # no python file created

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert len(reg) == 0


def test_load_from_directory_preflight_validation_failure(tmp_path: Path):
    """Verify bundle declaring streaming=True with empty generation_nodes fails pre-flight validation."""
    b_dir = tmp_path / "invalid_preflight_bundle"
    manifest = {
        "id": "invalid_preflight_wf",
        "name": "Invalid Preflight",
        "description": "Declares streaming=True but generation_nodes is empty",
        "entrypoint": "workflow:get_graph",
        "supports_streaming": True,
        "generation_nodes": [],
    }
    code = "def get_graph(): return lambda s: s"
    _create_bundle(b_dir, manifest_dict=manifest, code=code)

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)
    assert count == 0
    assert "invalid_preflight_wf" not in reg


def test_load_from_directory_partial_failure_resilience(tmp_path: Path):
    """Verify that corrupt or invalid bundles do not prevent valid bundles from loading."""
    # 1. Valid bundle 1
    _create_bundle(
        tmp_path / "bundle_good_1",
        manifest_dict={
            "id": "good_wf_1",
            "name": "Good 1",
            "description": "Valid bundle 1",
            "entrypoint": "workflow:get_graph",
            "supports_streaming": True,
            "generation_nodes": ["gen1"],
        },
        code="def get_graph(): return lambda s: s",
    )

    # 2. Corrupt JSON
    _create_bundle(
        tmp_path / "bundle_corrupt_json",
        manifest_raw="{not valid json:",
        code="def get_graph(): pass",
    )

    # 3. Python Syntax Error
    _create_bundle(
        tmp_path / "bundle_syntax_err",
        manifest_dict={
            "id": "syntax_err",
            "name": "Syntax Error",
            "description": "Syntax error",
            "entrypoint": "workflow:get_graph",
            "supports_streaming": False,
        },
        code="def broken_func( : syntax error",
    )

    # 4. Valid bundle 2
    _create_bundle(
        tmp_path / "bundle_good_2",
        manifest_dict={
            "id": "good_wf_2",
            "name": "Good 2",
            "description": "Valid bundle 2",
            "entrypoint": "workflow:get_graph",
            "supports_streaming": False,
        },
        code="def get_graph(): return lambda s: s",
    )

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)

    # Must load exactly the 2 valid bundles without crashing
    assert count == 2
    assert reg.list_ids() == ["good_wf_1", "good_wf_2"]


def test_load_from_directory_duplicate_id_handling(tmp_path: Path):
    """Verify duplicate workflow IDs are rejected when override=False and updated when override=True."""
    b_dir_1 = tmp_path / "bundle_orig"
    _create_bundle(
        b_dir_1,
        manifest_dict={
            "id": "duplicate_id",
            "name": "Original Name",
            "description": "Original description",
            "entrypoint": "workflow:get_graph",
            "supports_streaming": False,
        },
        code="def get_graph(): return lambda s: s",
    )

    reg = WorkflowRegistry()
    count1 = reg.load_from_directory(b_dir_1)
    assert count1 == 1
    assert reg.get("duplicate_id").info.name == "Original Name"

    # Second bundle with same ID
    b_dir_2 = tmp_path / "bundle_replacement"
    _create_bundle(
        b_dir_2,
        manifest_dict={
            "id": "duplicate_id",
            "name": "Replacement Name",
            "description": "Replacement description",
            "entrypoint": "workflow:get_graph",
            "supports_streaming": False,
        },
        code="def get_graph(): return lambda s: s",
    )

    # override=False: should fail and retain original
    count2 = reg.load_from_directory(b_dir_2, override=False)
    assert count2 == 0
    assert reg.get("duplicate_id").info.name == "Original Name"

    # override=True: should replace successfully
    count3 = reg.load_from_directory(b_dir_2, override=True)
    assert count3 == 1
    assert reg.get("duplicate_id").info.name == "Replacement Name"


def test_load_from_directory_with_sibling_imports(tmp_path: Path):
    """Verify dynamic loading supports sibling module imports within the bundle directory."""
    bundle_dir = tmp_path / "sibling_import_bundle"
    manifest = {
        "id": "sibling_wf",
        "name": "Sibling Workflow",
        "description": "Workflow importing a sibling helper module",
        "entrypoint": "workflow:get_graph",
        "supports_streaming": False,
    }
    helper_code = """
def build_output_message():
    return "Output from sibling helper module"
"""
    workflow_code = """
import helper

def get_graph():
    def _runnable(state):
        return {"answer": helper.build_output_message()}
    return _runnable
"""
    _create_bundle(
        bundle_dir,
        manifest_dict=manifest,
        code=workflow_code,
        filename="workflow.py",
    )
    (bundle_dir / "helper.py").write_text(helper_code, encoding="utf-8")

    reg = WorkflowRegistry()
    count = reg.load_from_directory(tmp_path)

    assert count == 1
    assert "sibling_wf" in reg
    wf = reg.get("sibling_wf")
    graph = wf.get_compiled_graph()
    res = graph({})
    assert res["answer"] == "Output from sibling helper module"


@pytest.mark.asyncio
async def test_load_from_directory_end_to_end_execution(tmp_path: Path):
    """Verify complete end-to-end flow: discovery, initialization, graph execution, and output conversion."""
    bundle_dir = tmp_path / "e2e_bundle"
    manifest = {
        "id": "e2e_calc_wf",
        "name": "End-to-End Calculation Workflow",
        "description": "Complete pluggable workflow running a LangGraph graph",
        "entrypoint": "graph_factory:create_runnable",
        "is_conversational": False,
        "supports_streaming": True,
        "step_labels": {"process": "Performing calculation"},
        "generation_nodes": ["process"],
        "metadata": {"category": "math"},
    }
    graph_code = """
from langgraph.graph import StateGraph, START, END
from app.rag.workflows.base import StandardWorkflowState

def process_node(state: StandardWorkflowState):
    q = state.get("question", "")
    return {
        "answer": f"Processed: {q.upper()}",
        "source_used": "direct",
        "trace": ["Processed query to uppercase"],
        "retrieved_contexts": ["Calculation context"],
    }

def create_runnable():
    builder = StateGraph(StandardWorkflowState)
    builder.add_node("process", process_node)
    builder.add_edge(START, "process")
    builder.add_edge("process", END)
    return builder.compile()
"""
    _create_bundle(
        bundle_dir,
        manifest_dict=manifest,
        code=graph_code,
        filename="graph_factory.py",
    )

    reg = WorkflowRegistry()
    loaded = reg.load_from_directory(tmp_path)
    assert loaded == 1

    wf = reg.get("e2e_calc_wf")
    # Warmup
    reg.warmup()

    # Initial state conversion
    wf_input = WorkflowInput(
        question="calculate total",
        session_id="sess_123",
        trace_id="trace_999",
    )
    initial_state = wf.to_initial_state(wf_input, trace_id="trace_999")
    assert initial_state["question"] == "calculate total"
    assert initial_state["session_id"] == "sess_123"

    # Execution
    graph = wf.get_compiled_graph()
    final_state = await graph.ainvoke(initial_state)

    # Output conversion
    output = wf.to_workflow_output(final_state, trace_id="trace_999")
    assert isinstance(output, WorkflowOutput)
    assert output.workflow == "e2e_calc_wf"
    assert output.answer == "Processed: CALCULATE TOTAL"
    assert output.source_used == "direct"
    assert output.trace == ["Processed query to uppercase"]
    assert output.trace_id == "trace_999"

    # EvalContext extraction
    eval_ctx = wf.extract_eval_context(final_state)
    assert eval_ctx.source_used == "direct"
    assert eval_ctx.retrieved_contexts == ["Calculation context"]


def test_load_workflows_from_directory_helper_function(tmp_path: Path):
    """Verify module-level load_workflows_from_directory utility function."""
    b_dir = tmp_path / "helper_fn_bundle"
    manifest = {
        "id": "helper_fn_wf",
        "name": "Helper Function WF",
        "description": "Loaded via helper function",
        "entrypoint": "workflow:get_graph",
        "supports_streaming": False,
    }
    _create_bundle(b_dir, manifest_dict=manifest, code="def get_graph(): return lambda s: s")

    custom_reg = WorkflowRegistry()
    count = load_workflows_from_directory(tmp_path, registry=custom_reg)
    assert count == 1
    assert "helper_fn_wf" in custom_reg
