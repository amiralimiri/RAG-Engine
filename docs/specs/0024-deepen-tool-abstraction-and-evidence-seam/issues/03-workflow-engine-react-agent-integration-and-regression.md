# 03: Workflow Engine & ReAct Agent Integration with Regression Verification

**What to build:**
Integrate the deepened tool execution seam into the 10-node StateGraph engine (`app/rag/engine.py`) and autonomous ReAct agent (`app/rag/workflows/react_agent.py`). Simplify graph retrieval nodes (`node_retrieve_kb`, `node_search_web`) to use `await tool.aexecute()`, and delete the `_unpack_tool_output` helper. Refactor `ReActAgentWorkflow.to_workflow_output()` to use `aggregate_tool_evidence()`, pruning all regex scraping fallbacks and hardcoded tool name checks. Verify full regression across all workflows, streaming, memory, and evaluation test suites.

**Blocked by:**
Ticket 02: RAGTool Execution Seam, Concrete Adapters, and Zero-Shim Pruning

**Status:** ready-for-agent

## Acceptance Criteria

- [ ] `app/rag/engine.py` updated:
  - `node_retrieve_kb` calls `result = await active_kb_tool.aexecute({"query": search_query}, config=config)` and reads typed `result.content` and `result.artifact`.
  - `node_search_web` calls `result = await active_web_tool.aexecute({"query": search_query}, config=config)` and reads typed `result.content` and `result.artifact`.
  - `_unpack_tool_output` helper function completely deleted.
  - `generate_from_kb` and `generate_from_web` consume citations and contexts directly from `AgentState` without manual regex/scraping loops.
- [ ] `app/rag/workflows/react_agent.py` updated:
  - `to_workflow_output` calls `aggregate_tool_evidence(messages)` to extract citations, contexts, and dynamic `source_used`.
  - Obsolete `_extract_tools_called`, `_resolve_source_used`, and regex scraping fallbacks (`re.findall(r"\[Source:\s*([^\]]+)\]", ...)`, `re.findall(r"Title:\s*([^\n]+)\nURL:\s*([^\n]+)", ...)`) completely deleted.
- [ ] `tests/rag/test_react_agent.py` and `tests/rag/test_base_workflow.py` aligned to verify structured citation extraction from `ToolEvidenceArtifact` without string scraping.
- [ ] Full regression test suite (`pytest`) runs green across all test files (workflows, memory, evaluation, streaming, telemetry).
- [ ] Zero linting or formatting regressions (`ruff check`, `ruff format`).
