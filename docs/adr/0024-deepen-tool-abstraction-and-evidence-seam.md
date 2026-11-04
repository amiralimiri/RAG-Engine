# ADR 0024: Deepen Tool Abstraction and Evidence Seam

## Status
Accepted (Supersedes ADR 0008)

## Context
In ADR 0008, we introduced LangChain `BaseTool` instances configured with `response_format="content_and_artifact"` to bridge fixed DAG workflows and autonomous ReAct agents. However, three architectural fractures emerged over time:
1. **Heterogeneous, Untyped Dict Artifacts & Regex Fallbacks**: Tools returned ad-hoc dictionaries with inconsistent keys (`{"documents": ..., "citations": ...}` vs `{"citations": ..., "retrieved_contexts": ...}`). In `app/rag/workflows/react_agent.py`, the artifact type check failed on every execution, causing the workflow to silently fall back to regex scraping message strings (`re.findall(r"\[Source:\s*([^\]]+)\]", ...)`).
2. **Global Monkey-Patching for Config Forwarding**: `DualOutputStructuredTool` intercepted invocations to store `RunnableConfig` in a global `ContextVar` (`_TOOL_INVOKE_CONFIG`), passed synthetic `tool_call_id="__direct__"` into `super().run()`, and forced tools to fetch config via `get_invoke_config()`. Modern LangChain injects `RunnableConfig` natively without global state.
3. **Tight Workflow Coupling to Tool Identifiers**: `ReActAgentWorkflow` and `app/rag/engine.py` hardcoded specific tool names (`"knowledge_base_search"`, `"web_search"`) to resolve evidence sources and deduplicate citations. Adding a new tool required modifying workflow internals.

---

## Decisions

### 1. Canonical Domain Contracts: `ToolEvidenceArtifact`, `ToolResult`, and `Citation`
- Define `Citation` as a strongly-typed domain model (`title: str`, `url: str`, `type: str`).
- Define `ToolEvidenceArtifact` as the single canonical structured artifact emitted by all tools:
  - `citations: list[Citation]`
  - `retrieved_contexts: list[str]`
  - `source_used: str` (self-describing evidence identifier, e.g. `"vectorstore"`, `"web_search"`)
  - `raw_payload: Any = None` (optional raw underlying documents or provider payload)
- Define `ToolResult` as the container returned for direct DAG callers:
  - `content: str`
  - `artifact: ToolEvidenceArtifact`
  - Supports 2-tuple unpacking (`__iter__`) for ergonomic backward compatibility with `content, artifact = tool.execute(...)`.

### 2. Deep `RAGTool` Subclass and Zero-Shim Execution Seam
- Replace `DualOutputStructuredTool` with `RAGTool(StructuredTool)` in `app/rag/tools/base.py`.
- Expose explicit `execute(input_args, config=None) -> ToolResult` and async `aexecute(input_args, config=None) -> ToolResult` entrypoints for direct workflow callers, eliminating tuple/ToolMessage duck-typing and synthetic `tool_call_id` hacks.
- Rely strictly on native LangChain parameter injection for `config: RunnableConfig | None = None`. Completely prune `_TOOL_INVOKE_CONFIG`, `_UNSET`, and `get_invoke_config()`.
- For autonomous agents (`create_agent` / LangGraph `ToolNode`), `RAGTool` produces a standard `ToolMessage` whose `.artifact` attribute is the typed `ToolEvidenceArtifact`.
- Provide an in-memory `FakeRAGTool` test double for deterministic testing without external dependencies.

### 3. Dynamic Polymorphic Evidence Aggregation
- Centralize citation and context deduplication in `app/rag/tools/base.py` via `aggregate_tool_evidence(messages_or_artifacts) -> AggregatedEvidence`.
- Dynamically resolve `source_used`:
  - 0 tools executed &rarr; `"direct"`
  - 1 tool executed &rarr; tool artifact's declared `source_used`
  - 2+ distinct tools executed &rarr; `"react_agent"`
- Prune all hardcoded tool name checks and regex scraping loops from `app/rag/workflows/react_agent.py` and `app/rag/engine.py`. New tools can be plugged into any workflow without modifying workflow code.

---

## Consequences

### Positive
- **Locality**: Parsing, formatting, citation deduplication, and source resolution concentrate exclusively in `app/rag/tools/`.
- **Leverage**: Adding new tools (e.g., Jira, Confluence, GitHub) is 100% plug-and-play across all workflows and evaluation listeners.
- **Robustness**: Eliminates brittle regex scraping fallbacks and global `ContextVar` monkeypatching.
- **Testability**: In-memory `FakeRAGTool` provides clean, isolated test doubles without complex mock setups.

### Negative
- **Migration Surface**: All existing tools (`create_retriever_tool`, `create_web_search_tool`) and their test suites must be updated to return `ToolEvidenceArtifact` and use `RAGTool`.
