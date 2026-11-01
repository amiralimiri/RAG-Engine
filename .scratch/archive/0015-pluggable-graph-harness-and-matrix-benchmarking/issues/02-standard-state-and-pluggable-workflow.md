# 02: `StandardWorkflowState` & `PluggableWorkflow` Implementation

**What to build:**
Implement the standard state schema and generic pluggable workflow adapter:
1. Define `StandardWorkflowState` (`TypedDict`) in `app/rag/workflows/base.py` containing canonical keys:
   `question`, `messages`, `attachments`, `session_id`, `trace_id`, `answer`, `retrieved_contexts`, `source_used`, `citations`, `trace`, `rewritten_query`.
2. Implement `PluggableWorkflow` in `app/rag/workflows/base.py` (or a dedicated module `app/rag/workflows/pluggable.py`):
   - Accepts `info: WorkflowInfo` and `graph: CompiledStateGraph | Callable[[], CompiledStateGraph]`.
   - Implements `WorkflowDefinition` Protocol.
   - Converts `WorkflowInput` $\to$ `StandardWorkflowState`.
   - Converts `StandardWorkflowState` $\to$ `WorkflowOutput`, robustly extracting answers from `state["answer"]` or trailing `AIMessage` in `state["messages"]`.
   - Implements `extract_eval_context` and `handle_error` fallbacks.
3. Create `tests/rag/test_pluggable_workflow.py` using `BaseWorkflowContractTest` to verify that `PluggableWorkflow` passes all conformance checks across simple StateGraph, message-based graphs, and mock graphs.

**Blocked by:** 01: Domain DTOs (`WorkflowInput`, `WorkflowOutput`) & Transport Decoupling

**Status:** closed

- [x] `StandardWorkflowState` defined in `app/rag/workflows/base.py`
- [x] `PluggableWorkflow` class implemented satisfying `WorkflowDefinition`
- [x] Answer extraction supports direct string and `AIMessage` fallback
- [x] Error fallback produces valid fallback `WorkflowOutput` with error trace
- [x] Unit tests in `tests/rag/test_pluggable_workflow.py` passing via `BaseWorkflowContractTest`
- [x] Verification suite: `uv run pytest tests/rag/test_pluggable_workflow.py`
