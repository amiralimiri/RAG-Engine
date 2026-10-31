# Retrospective: Milestone 0014 (Modernize ReAct Agent to LangChain `create_agent`)

**Date:** October 31, 2026  
**Status:** Completed, Reviewed, Remediated, & Archived  
**ADR Reference:** `docs/adr/0014-modernize-react-agent-to-langchain-create-agent.md`  
**Specification:** `docs/specs/0014-modernize-react-agent-to-langchain-create-agent-spec.md`  
**Work Packages / Tickets:** `docs/specs/0014-modernize-react-agent-to-langchain-create-agent-tickets.md`  
**Domain Guidelines:** `docs/agents/workflow-execution.md` | `docs/agents/codebase-unification.md`  
**Test Suite:** 498 passed, 0 failed, 100% offline-capable, 0 `LangGraphDeprecatedSinceV10` warnings  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0014 modernized `ReActAgentWorkflow` from the deprecated `langgraph.prebuilt.create_react_agent` to LangChain's canonical `langchain.agents.create_agent` agent factory seam, eliminating all `LangGraphDeprecatedSinceV10` deprecation warnings and future-proofing the engine for LangGraph 2.0.

Beyond modernizing the import and compiler seam, this milestone harmonized evidence source resolution between client transport responses and offline evaluation contexts, implemented resilient query fallback extraction from conversational message history, dual-mapped node constants for seamless backward compatibility, and hardened SSE token streaming and step event filtering.

### Key Deliverables:
- **Canonical Agent Compiler Migration (`app/rag/workflows/react_agent.py`)**:
  - Replaced `create_react_agent` with `from langchain.agents import create_agent`.
  - Re-parameterized compiler call to use `system_prompt=self.system_prompt` (previously `prompt=...`).
  - Verified graph topology produces `['__start__', 'model', 'tools', '__end__']`.
  - Achieved **zero** `LangGraphDeprecatedSinceV10` deprecation warnings across all test suites.
- **Node Identifier Dual-Mapping**:
  - Updated `DEFAULT_REACT_GENERATION_NODES = {"agent", "model"}`.
  - Updated `DEFAULT_REACT_STEP_LABELS` to dual-map both `"agent"` and `"model"` to `"Reasoning & planning response..."` alongside `"tools"`.
  - Ensured streaming token deltas and UI step progress updates work identically whether executing against modern graphs (`model`) or legacy/mocked event streams (`agent`).
- **Resilient Query Resolution & Trace Attribution**:
  - Implemented conversational history traversal in `to_chat_response` to extract the user query from the latest `HumanMessage` when `final_state.get("question")` is omitted by modern graph output dictionaries.
  - Formatted transport metadata (`rewritten_query`) and trace log entries (`"Inquiry received: '...'"`).
- **Harmonized Evidence Source Resolution & Tool Message Duck-Typing**:
  - Extracted shared helper `_resolve_source_used(tools_called: Sequence[str], explicit_source: str | None = None) -> str`.
  - Extracted `_is_tool_message(m: Any) -> bool` to deduplicate tool message duck-typing across tool extraction, citation extraction, and evaluation context extraction.
  - Aligned evidence attribution across `ChatResponse` and `EvalContext` (`"error"` &rarr; `"direct"` in eval context).
- **Comprehensive Test Suite & Conformance Gate**:
  - Added node topology and zero-warning unit tests in `tests/rag/test_react_agent.py`.
  - Consolidated and parametrized ReAct streaming tests across `"agent"` and `"model"` nodes in `tests/test_chat_stream.py`.
  - Full test suite expanded to 498 passing tests with 100% green pass and zero regressions.

---

## 2. What Went Well

1. **Seamless Drop-In Replacement via `create_agent`**:
   - `langchain.agents.create_agent` accepted identical checkpointers (`MemorySaver`), tools, and models without requiring changes to underlying tool definitions or the `WorkflowDefinition` interface contract.
2. **Dual-Mapping Resilience**:
   - Dual-mapping `DEFAULT_REACT_GENERATION_NODES` (`{"agent", "model"}`) and `DEFAULT_REACT_STEP_LABELS` prevented breaking changes across existing test doubles, mocked event streams, and frontend consumers while allowing modern node names to function immediately.
3. **Harmonized Evaluation Context**:
   - Sharing `_resolve_source_used` across `to_chat_response` and `extract_eval_context` resolved subtle source attribution divergences between client responses and offline evaluation benchmarks.
4. **Code Review & Remediation Loop**:
   - The parallel Standards and Spec `/code-review` run surfaced baseline smells (duplicated streaming tests and tool message duck-typing) which were immediately refactored into parametrized tests and private helper functions before final retrospective and archiving.

---

## 3. Friction Points & Challenges Encountered

1. **State Dictionary Schema Shifts (`question` omission)**:
   - *Problem*: `create_agent` returns a state dictionary containing `{"messages": [...]}` but omits custom input keys such as `"question"`.
   - *Root Cause*: LangChain's standardized `AgentState` schema manages conversation state entirely through messages rather than custom input passthroughs.
   - *Resolution*: Implemented fallback traversal in `to_chat_response` that inspects `reversed(messages)` to extract content from the latest `HumanMessage`.
2. **LLM Node Renaming (`agent` vs `model`)**:
   - *Problem*: `WorkflowRunner.stream()` checks `langgraph_node in workflow.generation_nodes` to yield token deltas and `step_labels` for UI indicators. In `create_agent`, this node changed from `"agent"` to `"model"`.
   - *Root Cause*: Upstream LangChain/LangGraph naming standardization for graph nodes.
   - *Resolution*: Dual-mapped both node identifiers in `DEFAULT_REACT_GENERATION_NODES` and `DEFAULT_REACT_STEP_LABELS`, ensuring complete backward and forward compatibility.

---

## 4. Codified Agent Guardrails & Updated Rules

The following guidelines have been codified in `docs/agents/workflow-execution.md`:

1. **Canonical Agent Factory**: Always compile autonomous ReAct agents using `from langchain.agents import create_agent` with `system_prompt=...`. Never import or invoke deprecated `langgraph.prebuilt.create_react_agent`.
2. **Node Identifier Dual-Mapping**: Maintain `{"agent", "model"}` in generation nodes and step labels for ReAct workflows to support both modern and legacy/mock node topologies.
3. **Resilient Query Extraction**: When converting graph output states to domain responses (`to_chat_response`), extract user queries from conversational history (`HumanMessage`) if absent from the top-level state dictionary.
4. **Harmonized Evidence Resolution**: Route evidence source resolution through `_resolve_source_used(tools_called=...)` to ensure strict parity between `ChatResponse.source_used` and `EvalContext.source_used`.
5. **Tool Message Duck-Typing**: Use `_is_tool_message(m)` (`isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool"`) to identify tool outputs across all workflow state transformers.

---

## 5. Artifact & Ticket Archive Status

All 3 work packages have been archived to `.scratch/archive/0014-modernize-react-agent-to-langchain-create-agent/issues/`:

| Ticket | Scope | Status |
|---|---|---|
| **Ticket 01** | Node Constants, Source Resolution Harmonization & Query Fallback | Completed & Archived |
| **Ticket 02** | Canonical `create_agent` Compiler Migration | Completed & Archived |
| **Ticket 03** | End-to-End Streaming, Telemetry & Full Conformance Gate | Completed & Archived |

Active tickets in `.scratch/` are pruned and archived.

---

## 6. Future Architectural Opportunities

1. **Pruning Legacy Node Compatibility (`agent` -> pure `model`)**:
   - In a future milestone once external consumers and all test doubles are confirmed upgraded, remove `"agent"` from `DEFAULT_REACT_GENERATION_NODES` and `DEFAULT_REACT_STEP_LABELS` per Zero-Shim principles.
2. **Declarative Workflow Topologies & Workflow Registry Modernization**:
   - Transition workflows from imperative class definitions to declarative graph definitions with composable prompt, tool, and checkpointer injectors.
3. **Unified Durable Checkpointers**:
   - Expand checkpointer support across workflows to include persistent backends (SQLite/PostgreSQL) in addition to in-memory `MemorySaver`.
