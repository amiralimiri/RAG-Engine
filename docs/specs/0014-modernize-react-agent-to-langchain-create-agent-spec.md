# Technical Specification: Modernize ReAct Agent to LangChain `create_agent`

- **ADR Reference:** `docs/adr/0014-modernize-react-agent-to-langchain-create-agent.md`
- **Target Version:** `0.14.0`
- **Status:** Proposed
- **Triage Label:** `ready-for-agent`
- **Work Packages / Tickets:** `docs/specs/0014-modernize-react-agent-to-langchain-create-agent-tickets.md`

---

## 1. Problem Statement

In `app/rag/workflows/react_agent.py`, the autonomous ReAct agent is compiled using `langgraph.prebuilt.create_react_agent`. In LangGraph 1.0, this factory is deprecated with `LangGraphDeprecatedSinceV10: create_react_agent has been moved to langchain.agents. Please update your import to from langchain.agents import create_agent. Deprecated in LangGraph V1.0 to be removed in V2.0.`

Beyond eliminating the deprecation warning, migrating to `langchain.agents.create_agent` introduces specific architectural shifts:
1. **Compilation Seam & Parameterization**:
   - `create_agent` parameterizes system instructions via `system_prompt` rather than `prompt`.
2. **Node Topology & Streaming Token Delta Filtering**:
   - `create_react_agent` identifies the LLM reasoning node as `"agent"`.
   - `create_agent` identifies the LLM reasoning node as `"model"`.
   - `WorkflowRunner.stream()` relies on `workflow.generation_nodes` to filter conversational token deltas, and `workflow.step_labels` to emit user-facing progress updates. If node sets are not updated, tokens and step labels are suppressed.
3. **Graph Output Schema & Resilient Query Extraction**:
   - `create_agent` returns a standardized dictionary with `messages` (and optional `structured_response`), omitting custom input passthrough fields (e.g. `question`). `to_chat_response` must robustly extract user query context from the conversation history.
4. **Evidence Attribution Consistency**:
   - Evidence source resolution (`source_used`) between `to_chat_response` and `extract_eval_context` was slightly divergent, risking mismatched evaluation attributes during benchmark runs.

---

## 2. Solution

Migrate `ReActAgentWorkflow` directly to `from langchain.agents import create_agent` while maintaining strict backward compatibility with transport schemas (`ChatRequest`, `ChatResponse`), streaming protocols, and existing mock test suites:
1. **Canonical Agent Factory**:
   - Replace `create_react_agent` with `create_agent(model=..., tools=..., checkpointer=..., system_prompt=...)`.
2. **Node Identifier Dual-Mapping**:
   - Update `DEFAULT_REACT_GENERATION_NODES` to `{"agent", "model"}`.
   - Update `DEFAULT_REACT_STEP_LABELS` to map both `"agent"` and `"model"` to `"Reasoning & planning response..."`.
3. **Resilient Query & Trace Extraction**:
   - In `to_chat_response`, extract the user question from the latest `HumanMessage` in `messages` if `final_state.get("question")` is missing.
4. **Harmonized Source Resolution**:
   - Extract a private helper `_resolve_source_used(messages, tools_called, explicit_source)` shared identically by `to_chat_response` and `extract_eval_context`.
5. **Comprehensive TDD Test Suite**:
   - Expand `tests/rag/test_react_agent.py` to assert graph node topology (`model`, `tools`), node dual-mapping, citation extraction, and zero deprecation warnings.

---

## 3. User Stories

1. **As an application maintainer**, I want `rag-engine` to compile ReAct agents using `langchain.agents.create_agent` so that the codebase runs with zero `LangGraphDeprecatedSinceV10` warnings and is fully prepared for LangGraph 2.0.
2. **As an API client**, I want to stream responses from `/api/chat/stream` with `workflow="react_agent"` and receive all conversational tokens and step updates seamlessly without regression.
3. **As an observability engineer**, I want ReAct agent traces to consistently record `"Inquiry received: '...'"` and tool execution steps even when modern LangGraph graph states return only message sequences.
4. **As an evaluation engineer**, I want `WorkflowRunner.execute_with_context()` to return harmonized `source_used` (`vectorstore`, `web_search`, `react_agent`, or `direct`) across both `ChatResponse` and `EvalContext`.
5. **As a test author**, I want existing mock fixtures and streaming tests using `"agent"` node names to continue passing green alongside modern `"model"` node runs.

---

## 4. Implementation Decisions

### 4.1. Factory Seam & Node Identifiers (`app/rag/workflows/react_agent.py`)

```python
from langchain.agents import create_agent

DEFAULT_REACT_STEP_LABELS: dict[str, str] = {
    "agent": "Reasoning & planning response...",
    "model": "Reasoning & planning response...",
    "tools": "Executing selected tool...",
}

DEFAULT_REACT_GENERATION_NODES: set[str] = {"agent", "model"}

DEFAULT_REACT_PROMPT: str = (
    "You are an expert Enterprise IT Support Assistant. "
    "Consult the internal knowledge base with knowledge_base_search first. "
    "If internal documentation is insufficient, use web_search. "
    "Provide clear, accurate, and direct answers citing your sources."
)
```

In `get_compiled_graph`:
```python
self._compiled_graph = create_agent(
    model=active_llm,
    tools=active_tools,
    checkpointer=active_checkpointer,
    system_prompt=self.system_prompt,
)
```

### 4.2. Harmonized Evidence Source Resolution

```python
def _resolve_source_used(
    messages: list[BaseMessage],
    tools_called: list[str],
    explicit_source: str | None = None,
) -> str:
    if explicit_source in ("error", "error_fallback"):
        return "error"
    if "knowledge_base_search" in tools_called and "web_search" in tools_called:
        return "react_agent"
    if "knowledge_base_search" in tools_called:
        return "vectorstore"
    if "web_search" in tools_called:
        return "web_search"
    if tools_called:
        return "react_agent"
    return explicit_source or "direct"
```

### 4.3. Resilient Query Resolution in `to_chat_response`

```python
question = final_state.get("question")
if not question:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            question = m.content if isinstance(m.content, str) else str(m.content)
            break
```

---

## 5. Invariants & Acceptance Criteria

1. **Zero Deprecation Warnings**:
   - Running `pytest -k react_agent` or running the full test suite produces **0** `LangGraphDeprecatedSinceV10` warnings.
2. **Node Identifier Invariant**:
   - `DEFAULT_REACT_GENERATION_NODES` contains both `"agent"` and `"model"`.
   - `DEFAULT_REACT_STEP_LABELS` contains `"agent"`, `"model"`, and `"tools"`.
3. **Execution Invariance**:
   - All 489 existing tests pass without regressions across sync execution, multi-turn memory persistence, token streaming, and Langfuse telemetry spans.
4. **Seam Encapsulation**:
   - Transport schemas (`ChatRequest`, `ChatResponse`) and public `WorkflowDefinition` contracts remain untouched.
5. **Evaluation Harmony**:
   - For any ReAct execution, `execution_result.response.source_used == execution_result.eval_context.source_used` (except for `"error"` which normalizes to `"direct"` in eval context).

---

## 6. Work Breakdown Structure (Tickets)

The implementation is broken down into 3 tracer-bullet tickets:

- **Ticket 01: Node Constants, Source Resolution Harmonization & Unit Tests**
  - Update `DEFAULT_REACT_STEP_LABELS` and `DEFAULT_REACT_GENERATION_NODES` to dual-map `"agent"` and `"model"`.
  - Add `_resolve_source_used` helper in `react_agent.py` and unify `to_chat_response` and `extract_eval_context`.
  - Implement query fallback resolution in `to_chat_response`.
  - Add unit tests verifying node constants, source resolution, and inquiry extraction.

- **Ticket 02: Canonical `create_agent` Compiler Migration**
  - Migrate import in `app/rag/workflows/react_agent.py` from `langgraph.prebuilt.create_react_agent` to `langchain.agents.create_agent`.
  - Update compiler call to use `system_prompt=self.system_prompt`.
  - Verify compiled graph structure (`model`, `tools` nodes).
  - Verify zero `LangGraphDeprecatedSinceV10` warnings emitted.

- **Ticket 03: End-to-End Streaming, Telemetry & Full Conformance Gate**
  - Verify streaming token deltas and step labels over SSE for ReAct agent.
  - Verify multi-turn checkpointer persistence across HTTP and runner seams.
  - Run full test suite (`uv run pytest`) across all 489+ tests with 100% green pass and zero regressions.
