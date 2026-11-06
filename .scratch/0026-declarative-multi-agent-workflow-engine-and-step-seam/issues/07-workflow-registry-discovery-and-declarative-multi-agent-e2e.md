# 07: WorkflowRegistry Discovery & Declarative Multi-Agent E2E

**What to build:**
Automatic discovery of `.yaml` workflows in `WorkflowRegistry` and a complete end-to-end multi-agent IT support scenario test.
Updates `WorkflowRegistry.load_from_directory()` to scan for both `manifest.json` bundles and `*.yaml` / `*.yml` workflow manifests, compiling and registering them automatically.
Introduces a canonical declarative multi-agent YAML manifest (`it_support_multi_agent.yaml`) configuring a triage agent that inspects inquiries and hands off to either a security specialist or an IT knowledge specialist.
Adds an end-to-end integration test suite verifying autonomous agent handoffs, side-by-side benchmarking against legacy workflows, and full compatibility with `WorkflowRunner` execution and streaming.

**Blocked by:**
06: Declarative YAML Schema & WorkflowCompiler

**Status:**
ready-for-agent

- [ ] Update `WorkflowRegistry.load_from_directory()` in `app/rag/workflows/registry.py` to scan for `.yaml`/`.yml` workflow files alongside `manifest.json`.
- [ ] Add a sample declarative multi-agent workflow manifest in `tests/fixtures/workflows/it_support_multi_agent.yaml`.
- [ ] Add end-to-end integration test suite in `tests/rag/workflows/test_declarative_workflows_e2e.py` testing:
  - Dynamic discovery and registration via `WorkflowRegistry`.
  - Multi-agent execution where Triage agent invokes `transfer_to_security` handoff tool, routing execution to Security agent.
  - Verification of `active_agent`, citations, and step transitions in `state["trace"]`.
  - Compatibility with `WorkflowRunner.execute()` and `WorkflowRunner.stream()`.
- [ ] Verify complete project test suite passes with `pytest tests/`.
- [ ] Pass `ruff check .` and `pyright .` static verification gates.
