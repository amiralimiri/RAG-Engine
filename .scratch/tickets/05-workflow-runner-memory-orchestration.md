# Ticket 05: WorkflowRunner Execution Pipeline Memory Hydration, Safe Rewind & Persistence

- **Milestone:** Unified Pluggable Conversation Memory (ADR-0016)
- **Status:** Pending
- **Dependency:** Ticket 01, Ticket 02, Ticket 03, Ticket 04
- **Estimated Scope:** Medium

---

## 1. Goal & Context
Centralize multi-turn memory lifecycle orchestration in `WorkflowRunner` (`app/rag/workflows/runner.py`):
1. Inject `ConversationMemoryAdapter` as a dependency in `WorkflowRunner.__init__`, defaulting to `get_conversation_memory()`.
2. In `_prepare_execution`: If `session_id` is present, hydrate bounded history from `memory` into `WorkflowInput.messages`.
3. Support safe pre-flight hydration for rewind: when `request.rewind_turn_index = k` is provided, hydrate only turns `[0 .. k-1]` without mutating the storage yet.
4. In `_finalize_execution`:
   - If execution succeeded and produced a valid answer:
     - If `rewind_turn_index` was specified, commit truncation `await memory.atruncate_from(session_id, rewind_turn_index)`.
     - Persist the new turn `await memory.asave_turn(turn)`.
   - Strictly skip turn persistence on client disconnects or unhandled exceptions.
5. Filter out intermediate agent scratchpads: persist only canonical `(HumanMessage, AIMessage)` semantic turns.
6. Bind memory telemetry (`turn_count`, `history_turns_loaded`) to structlog contextvars and Langfuse trace metadata.

---

## 2. Technical Requirements

### 2.1 Dependency Injection in `WorkflowRunner` (`app/rag/workflows/runner.py`)
- Add `memory: ConversationMemoryAdapter | None = None` to `WorkflowRunner.__init__`.
- Provide getter/setter property for `memory`.

### 2.2 Hydration in `_prepare_execution`
- If `request.session_id` is provided:
  - If `request.rewind_turn_index is not None`:
    - Fetch turns: `all_turns = await self.memory.aget_turns(request.session_id)`.
    - Sliced turns: `sliced = all_turns[:request.rewind_turn_index]`.
    - Hydrate messages: `[msg for t in sliced for msg in t.to_messages()]`.
  - Else:
    - Bounded hydration: `await self.memory.aget_messages(request.session_id, limit=settings.memory_max_turns)`.
  - If hydrated messages exist:
    - Assign to `request.messages`.
  - Bind `turn_count = len(request.messages) // 2` to `structlog.contextvars`.
  - Add `turn_count` to Langfuse telemetry metadata and tags.

### 2.3 Atomic Persistence in `_finalize_execution` & `_stream_generator`
- If `request.session_id` is present and answer is non-empty:
  - If `request.rewind_turn_index is not None`:
    - `await self.memory.atruncate_from(request.session_id, request.rewind_turn_index)`.
    - `turn_index = request.rewind_turn_index`.
  - Else:
    - `existing_turns = await self.memory.aget_turns(request.session_id)`.
    - `turn_index = len(existing_turns)`.
  - Construct `turn = ConversationTurn(session_id=..., turn_index=turn_index, user_query=request.question, assistant_answer=domain_output.answer, attachments=request.attachments or [], citations=domain_output.citations, source_used=domain_output.source_used, trace_id=resolved_tid)`.
  - `await self.memory.asave_turn(turn)`.
  - Log `conversation_turn_persisted` event.
- In `_stream_generator`:
  - When all tokens are yielded, call the persistence routine before yielding the final metadata frame.
  - If `CancelledError` or unhandled error occurs, abort persistence.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- Create `tests/rag/test_workflow_runner_memory.py`:
  - `test_runner_hydrates_history_across_turns`: Run turn 1, verify persisted. Run turn 2, verify turn 1 is hydrated into graph state.
  - `test_runner_skips_intermediate_tool_scratchpads`: Execute `react_agent` workflow calling tools. Verify memory contains only user query and assistant answer, zero `ToolMessage` entries.
  - `test_runner_atomic_safe_rewind`: Create a 3-turn session. Execute turn 2 with `rewind_turn_index = 1`. Verify history at index 1 is replaced and index 2 is removed.
  - `test_runner_safe_rewind_preserves_on_failure`: Execute with `rewind_turn_index = 1` and mock an inference failure. Verify original turns 1 and 2 remain intact in storage!
  - `test_runner_streaming_persists_on_success`: Run `runner.stream(...)`, consume stream, verify turn saved.
  - `test_runner_streaming_aborts_persistence_on_client_cancel`: Trigger premature cancellation on generator; verify partial turn is NOT saved.

### Green (Implementation)
- Update `app/rag/workflows/runner.py`.

### Refactor
- Ensure synchronous `execute()` and asynchronous `aexecute()` both cleanly await/dispatch memory operations without event loop deadlock.
