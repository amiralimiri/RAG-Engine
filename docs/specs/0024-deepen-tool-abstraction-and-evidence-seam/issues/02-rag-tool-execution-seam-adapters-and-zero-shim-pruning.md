# 02: RAGTool Execution Seam, Concrete Adapters, and Zero-Shim Pruning

**What to build:**
Implement the deep `RAGTool(StructuredTool)` subclass with explicit `.execute()` and async `.aexecute()` methods returning `ToolResult`. Refactor concrete adapters (`create_retriever_tool`, `create_web_search_tool`) to instantiate `RAGTool`, return `(content, ToolEvidenceArtifact)`, and receive `RunnableConfig` via native LangChain parameter injection. Introduce `FakeRAGTool` as an in-memory test double. Completely prune legacy shims (`DualOutputStructuredTool`, `_TOOL_INVOKE_CONFIG`, `_UNSET`, `get_invoke_config()`) and update `app/rag/tools/__init__.py` to export the unified tool API.

**Blocked by:**
Ticket 01: Canonical Domain Models and Polymorphic Evidence Aggregator

**Status:** ready-for-agent

## Acceptance Criteria

- [ ] `RAGTool` implemented extending `StructuredTool`:
  - `aexecute(input_args, config=None) -> ToolResult`: natively executes tool and wraps output into `ToolResult`.
  - `execute(input_args, config=None) -> ToolResult`: synchronous counterpart.
  - Standard `.ainvoke()` / `.invoke()` when invoked by LangGraph `ToolNode` / `create_agent` produces `ToolMessage` with `ToolEvidenceArtifact` on `.artifact`.
- [ ] Factory function `rag_tool(func, coroutine, name, description, args_schema) -> RAGTool` implemented with `response_format="content_and_artifact"`.
- [ ] `FakeRAGTool(RAGTool)` in-memory test double implemented with configurable `content` and `artifact`, and call recording (`execute_calls`, `aexecute_calls`).
- [ ] `create_retriever_tool` refactored:
  - Returns `RAGTool` instance with `args_schema=KnowledgeSearchInput`.
  - Core functions `_run` and `_arun` accept `config: RunnableConfig | None = None` natively and return `(content, ToolEvidenceArtifact)`.
  - Constructs `ToolEvidenceArtifact` with `citations: list[Citation]`, `retrieved_contexts: list[str]`, `source_used="vectorstore"`, and `raw_payload=docs`.
- [ ] `create_web_search_tool` refactored:
  - Returns `RAGTool` instance with `args_schema=WebSearchInput`.
  - Core functions `_run` and `_arun` accept `config: RunnableConfig | None = None` natively and return `(content, ToolEvidenceArtifact)`.
  - Constructs `ToolEvidenceArtifact` with `citations: list[Citation]`, `retrieved_contexts: list[str]`, `source_used="web_search"`, and `raw_payload=result`.
- [ ] Zero-Shim Pruning:
  - Completely remove `DualOutputStructuredTool`, `_TOOL_INVOKE_CONFIG`, `_UNSET`, and `get_invoke_config()` from `app/rag/tools/base.py`.
- [ ] `app/rag/tools/__init__.py` cleanly exports `RAGTool`, `rag_tool`, `FakeRAGTool`, `Citation`, `ToolEvidenceArtifact`, `ToolResult`, `AggregatedEvidence`, `aggregate_tool_evidence`, `create_retriever_tool`, `create_web_search_tool`, `KnowledgeSearchInput`, and `WebSearchInput`.
- [ ] Tool tests in `tests/rag/test_tools.py` updated to verify `RAGTool.execute`, `RAGTool.aexecute`, `FakeRAGTool`, and pure config injection without monkey-patching.
