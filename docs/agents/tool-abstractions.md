# Tool Abstractions & Seam Patterns

Guidelines and lessons learned from ADR-0008 (Unified `BaseTool` Abstraction Seam) for designing, consuming, and testing tools across fixed DAG workflows and autonomous ReAct agents.

---

## 1. Core Principles

1. **Single Source of Truth**:
   - Provider clients (Tavily, Pinecone vectorstore, etc.), string formatting, and citation generation logic belong exclusively in `app/rag/tools/`.
   - Never implement ad-hoc parsing or client calls inside graph nodes or workflow orchestrators.

2. **Dual-Output Contract (`content_and_artifact`)**:
   - All tools configure `response_format="content_and_artifact"`.
   - Return values consist of:
     - `content: str`: Human-readable prose or formatted chunks for LLM prompt synthesis.
     - `artifact: dict[str, Any]`: Structured dictionary (e.g. `{"documents": list[Document], "citations": list[dict]}`, or `{"citations": list[dict], "retrieved_contexts": list[str]}`) for direct state mapping and online evaluation.

3. **Native Asynchronous Execution**:
   - Tools must provide both synchronous `_run` and asynchronous `_arun` implementations.
   - All I/O nodes in `app/rag/engine.py` are native coroutines invoking `await tool.ainvoke(..., config=config)`.
   - Always type-hint configs as `config: RunnableConfig | None = None` (strictly avoiding `config: RunnableConfig = None`).

---

## 2. Friction Points & Architectural Solutions

### 2.1. LangChain Direct Invocation & `DualOutputStructuredTool`
- **Issue**: Standard `StructuredTool` from LangChain emits a `ToolMessage` when invoked by an agent inside an LLM tool-calling loop (where `tool_call_id` is supplied). However, when called directly via `tool.invoke` or `tool.ainvoke` without `tool_call_id` (such as in fixed DAG nodes or unit tests), `StructuredTool` defaults to returning only the string `content` or fails to unpack the tuple.
- **Solution**: Use `DualOutputStructuredTool` (`app/rag/tools/base.py`). It intercepts direct invocations (`tool_call_id is None`) and unpacks `(message.content, message.artifact)` cleanly.

### 2.2. The Falsy Value Trap in Artifact Unpacking
- **Issue**: Checking truthiness (`if raw_output.artifact:`) causes valid empty search results (`artifact={}`) to evaluate to `False`. The unpacking logic would then fall through to the string fallback (`else: content, artifact = str(raw_output), {}`), corrupting valid messages into stringified objects.
- **Solution**: Always check for non-None existence:
  ```python
  if isinstance(raw_output, tuple) and len(raw_output) == 2:
      content, artifact = raw_output
  elif hasattr(raw_output, "artifact") and getattr(raw_output, "artifact") is not None:
      content, artifact = getattr(raw_output, "content", ""), getattr(raw_output, "artifact")
  else:
      content, artifact = str(raw_output), {}
  ```

### 2.3. Dependency Injection vs. Legacy Monkeypatching
- **Issue**: Legacy unit tests frequently monkeypatched globals in `app.rag.engine` (e.g. `patch("app.rag.engine.get_retriever", ...)` or `patch("app.rag.engine.web_search_tool", ...)`). If new graph nodes decouple into tool instances resolved at graph construction time, global patches can be bypassed or require middle-man adapter shims.
- **Solution**:
  - Prefer explicit dependency injection in all new tests: pass mock `BaseTool` instances directly to `create_agentic_rag_graph(..., kb_tool=mock_kb, web_tool=mock_web)`.
  - For legacy backward compatibility, tool factory helpers (`_get_active_retriever()`, `_get_active_client()`) lazily inspect module scopes for monkeypatches without leaking ad-hoc adapter classes into the workflow engine.

### 2.4. Transparent Exception Propagation
- **Issue**: Swallowing provider exceptions (e.g. returning empty lists on network timeout) causes the graph to misdiagnose infrastructure failures as "insufficient knowledge," silently returning ungrounded answers.
- **Solution**: Allow provider exceptions (e.g. `ConnectionError`, `RateLimitError`) to bubble up through `tool.invoke` and `tool.ainvoke`. The workflow runner's centralized `handle_error` hook will catch the error, record error telemetry in Langfuse, and output a clean user-facing fallback.
