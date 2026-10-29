# 04: Parameterize SelfCorrectiveRagWorkflow with Tool Seam

**What to build:**
Update `SelfCorrectiveRagWorkflow` in `app/rag/workflows/self_corrective.py` to accept optional `kb_tool: BaseTool | None = None` and `web_tool: BaseTool | None = None` parameters in `__init__`, forwarding them directly to `create_agentic_rag_graph`. Verify default workflow registrations in `app/rag/workflows/__init__.py` compile cleanly and pass the `BaseWorkflowContractTest` conformance test suite.

**Blocked by:** 03-engine-tool-injection-async

**Status:** completed

- [x] Red: Add test in `tests/rag/test_self_corrective_contract.py` asserting `SelfCorrectiveRagWorkflow` instantiated with custom `kb_tool` and `web_tool` complies with `WorkflowDefinition` contracts.
- [x] Green: Update `SelfCorrectiveRagWorkflow.__init__` and `get_compiled_graph` in `app/rag/workflows/self_corrective.py` to forward `kb_tool` and `web_tool`.
- [x] Green: Ensure default registrations in `app/rag/workflows/__init__.py` compile without error.
- [x] Refactor: Run conformance test suite to ensure all assertions pass.
