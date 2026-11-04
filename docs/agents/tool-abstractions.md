# Tool Abstractions & Seam Patterns

Guidelines and architecture standards from ADR-0024 (Deepen Tool Abstraction and Evidence Seam, superseding ADR-0008) for designing, consuming, and testing tools across fixed DAG workflows and autonomous ReAct agents.

---

## 1. Core Principles

1. **Single Source of Truth**:
   - Provider clients (Tavily, Pinecone vectorstore, etc.), string formatting, citation generation, and evidence aggregation belong exclusively in `app/rag/tools/`.
   - Never implement ad-hoc parsing, regex scraping, or client calls inside graph nodes or workflow orchestrators.

2. **Canonical Domain Contracts**:
   - Every tool returns a human-readable display string and a strongly-typed `ToolEvidenceArtifact`:
     - `citations: list[Citation]`: Strongly-typed citations (`title`, `url`, `type`).
     - `retrieved_contexts: list[str]`: Formatted snippet strings ready for prompt inclusion or evaluation.
     - `source_used: str`: Canonical self-describing source tag (e.g., `"vectorstore"`, `"web_search"`).
     - `raw_payload: Any`: Optional underlying provider payload (e.g. `list[Document]`).

3. **Unified Execution Seam (`RAGTool`)**:
   - All tools subtype or instantiate `RAGTool` (extending LangChain's `StructuredTool`).
   - For direct DAG nodes and batch callers: invoke `await tool.aexecute(input, config=config)` (or synchronous `.execute()`) returning `ToolResult(content, artifact)`. `ToolResult` supports 2-tuple unpacking (`content, artifact = res`) for ergonomic consumption.
   - For autonomous ReAct agents (`create_agent` / LangGraph `ToolNode`): tools are invoked normally via `.ainvoke()`, producing a `ToolMessage` whose `.artifact` is the typed `ToolEvidenceArtifact`.

4. **Native Asynchronous Execution & Pure Config Injection**:
   - Tools expose both synchronous `_run` and asynchronous `_arun` implementations.
   - All I/O nodes in `app/rag/engine.py` are native coroutines invoking `await tool.aexecute(...)`.
   - Type-hint configs as `config: RunnableConfig | None = None`. LangChain natively injects the runtime config into `_run`/`_arun` without global `ContextVar` monkey-patches.

5. **Decoupled Evidence Aggregation (`aggregate_tool_evidence`)**:
   - Workflows never inspect tool names (e.g. `"knowledge_base_search"`) to determine `source_used` or scrape citations.
   - Workflows pass message history or artifact lists to `aggregate_tool_evidence(messages)`, which resolves deduplicated citations, contexts, and dynamic source attribution polymorphically.

---

## 2. Testing & Test Doubles

1. **In-Memory Fake Double (`FakeRAGTool`)**:
   - Use `FakeRAGTool` from `app/rag/tools/` for deterministic unit testing without network I/O or global singleton patching.
   - Configurable returns: easily set `content`, `citations`, `retrieved_contexts`, `source_used`, and record call arguments.

2. **Transparent Exception Propagation**:
   - Provider exceptions (`ConnectionError`, `RateLimitError`) bubble up through `tool.aexecute()` and `tool.ainvoke()` directly to LangGraph rather than being swallowed as empty results.
   - Centralized `WorkflowDefinition.handle_error` hooks handle telemetry and fallback gracefully.
