# ADR 0014: Modernize ReAct Agent to LangChain `create_agent`

## Status
Accepted (Extends ADR 0007, ADR 0010, and ADR 0011)

## Context
In ADR-0007, the system established `ReActAgentWorkflow` using `langgraph.prebuilt.create_react_agent` to provide multi-turn autonomous tool-calling capabilities with `MemorySaver` checkpointer memory.

In LangGraph 1.0, `create_react_agent` has been deprecated (`LangGraphDeprecatedSinceV10`) and slated for removal in LangGraph 2.0, with the canonical successor being `langchain.agents.create_agent`.

Migrating to `create_agent` entails structural adjustments:
1. **Compilation Seam & Parameterization**:
   - `create_agent` replaces `prompt=...` with `system_prompt=...`.
2. **Node Topology & Streaming Token Delta Filtering**:
   - In `create_react_agent`, the reasoning LLM node was identified as `"agent"`.
   - In `create_agent`, the reasoning LLM node is identified as `"model"`.
   - `WorkflowRunner.stream()` uses `workflow.generation_nodes` to identify which LangGraph nodes stream user-facing conversational token deltas, and `workflow.step_labels` to emit UI reasoning indicators.
3. **Graph Output Schema & Inquiry Extraction**:
   - `create_agent` returns a standardized `messages` list and optional `structured_response`, omitting passthrough dictionary keys (such as `question`). `ReActAgentWorkflow.to_chat_response` must robustly extract the active query from the message history.
4. **Evidence Attribution Alignment**:
   - Evidence source resolution (`source_used`) between `to_chat_response` and `extract_eval_context` must be strictly harmonized to ensure offline evaluation benchmarks receive identical metadata as client responses.

---

## Decisions

### 1. Adopt `langchain.agents.create_agent`
Replace `from langgraph.prebuilt import create_react_agent` with `from langchain.agents import create_agent` in `app/rag/workflows/react_agent.py`. The compiled graph will be instantiated with:
```python
self._compiled_graph = create_agent(
    model=active_llm,
    tools=active_tools,
    checkpointer=active_checkpointer,
    system_prompt=self.system_prompt,
)
```

### 2. Dual-Mapping for Generation Nodes & Step Labels
To seamlessly support both legacy event traces (or mocked stream events in existing test suites) and modern `create_agent` runtime topologies, define dual-mapping sets in `app/rag/workflows/react_agent.py`:
- `DEFAULT_REACT_GENERATION_NODES = {"agent", "model"}`
- `DEFAULT_REACT_STEP_LABELS = {"agent": "Reasoning & planning response...", "model": "Reasoning & planning response...", "tools": "Executing selected tool..."}`

### 3. Resilient Query Resolution in `to_chat_response`
When converting graph execution output to `ChatResponse`:
- If `final_state.get("question")` is missing, extract the inquiry from the latest `HumanMessage` in `final_state["messages"]`.
- Populate `ChatResponse.rewritten_query` and trace preamble (`"Inquiry received: '...'"`), ensuring transport consistency.

### 4. Harmonized Evidence Source Resolution
Create a shared helper `_resolve_source_used(messages: list[Any], tools_called: list[str], explicit_source: str | None = None) -> str` used identically by:
- `to_chat_response(...)`
- `extract_eval_context(...)`

Mapping logic:
- `explicit_source in ("error", "error_fallback")` &rarr; `"error"` (or `"direct"` in eval context)
- Both `knowledge_base_search` and `web_search` called &rarr; `"react_agent"`
- Only `knowledge_base_search` called &rarr; `"vectorstore"`
- Only `web_search` called &rarr; `"web_search"`
- Any other tools called &rarr; `"react_agent"`
- No tools called &rarr; `"direct"`

### 5. In-Memory Checkpointer & Test Isolation
Preserve support for `checkpointer` (defaulting to `MemorySaver()`), retaining multi-turn session persistence via `thread_id: session_id`. Ensure `tests/conftest.py:clean_registry` fixture provides clean isolation across test cases.

---

## Consequences

### Positive
- **Zero Deprecation Warnings**: Eliminates `LangGraphDeprecatedSinceV10` warnings across all test runs.
- **Future-Proof**: Conforms with LangGraph v2 and LangChain modern agent factory specifications.
- **Strict Seam Compatibility**: No breaking changes to `ChatRequest`, `ChatResponse`, `WorkflowDefinition`, or SSE streaming contracts.
- **Domain Consistency**: Harmonizes source attribution across live HTTP responses and evaluation context extraction.

### Negative / Trade-Offs
- `DEFAULT_REACT_GENERATION_NODES` retains `"agent"` alongside `"model"` for dual-mapping backward compatibility with existing stream mock fixtures.
