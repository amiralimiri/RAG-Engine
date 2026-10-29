# 02: WorkflowRunner SSE Streaming & Tool Delta Filtering

**What to build:** An asynchronous streaming generator in `WorkflowRunner` that consumes LangGraph `astream_events` v2, filters out internal tool-call deltas, formats SSE frames, manages client cancellations, flushes telemetry, and safely schedules background evaluation tasks across streaming responses.

**Blocked by:** 01: WorkflowRunner Core Execution & Unit Test Suite

**Status:** completed

- [x] Implement `WorkflowRunner.stream(request: ChatRequest) -> AsyncGenerator[str, None]` in `app/rag/workflows/runner.py`.
- [x] Implement helper methods `format_sse(event: str, data: dict[str, Any] | BaseModel) -> str` and `is_tool_call_chunk(chunk: Any) -> bool`.
- [x] Emit typed SSE frames matching existing contracts: `StreamStepEvent` on whitelisted step starts, `StreamTokenEvent` on non-tool generation chunks, `StreamMetadataEvent` on graph completion, and `StreamErrorEvent` on failures.
- [x] Implement fallback static answer synthesis when zero token chunks are streamed from terminal nodes (e.g. `insufficient`).
- [x] Safeguard background evaluation tasks in a module-level `set[asyncio.Task]` with completion callbacks to prevent premature garbage collection.
- [x] Handle client disconnect cancellations (`asyncio.CancelledError`, `GeneratorExit`) gracefully with structured cancellation logging.
- [x] Ensure telemetry callbacks and `flush_telemetry()` are executed in a `finally` block.
- [x] Add streaming unit tests to `tests/rag/test_workflow_runner.py` verifying step emissions, token filtering, metadata serialization, and error event handling.
