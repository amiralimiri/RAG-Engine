# Ticket 04: WorkflowRunner Caller Streamlining & Memory Hydration Unification

- **Milestone:** Deepen Conversation Memory Seam (ADR-0017)
- **Status:** ready-for-agent
- **Blocked by:** Ticket 02 (In-Memory Adapter), Ticket 03 (SQLite Adapter)
- **Estimated Scope:** Small

---

## 1. Goal & Context
Refactor `WorkflowRunner` (`app/rag/workflows/runner.py`) to consume the deepened `ConversationMemoryAdapter` seam. In `_apersist_turn`, delegate persistence entirely to `await self.memory.aappend_turn(...)`, removing caller-side index calculation, turn counting, and manual calls to `atruncate_from`. In `_ahydrate_memory`, collapse branched rewind slicing into a single call to `await self.memory.aget_messages(session_id, limit=max_turns, before_turn_index=rewind_turn_index)`.

---

## 2. Technical Requirements

### 2.1 Refactor `WorkflowRunner._apersist_turn` (`app/rag/workflows/runner.py`)
- Replace the existing turn counting, truncation, and `ConversationTurn` assembly:
  ```python
  # Old:
  rewind_turn_index = getattr(request, "rewind_turn_index", None)
  if rewind_turn_index is not None:
      await self.memory.atruncate_from(session_id, rewind_turn_index)
      turn_index = rewind_turn_index
  else:
      existing_turns = await self.memory.aget_turns(session_id)
      turn_index = len(existing_turns)
  turn = ConversationTurn(...)
  await self.memory.asave_turn(turn)
  ```
  With a single high-leverage call:
  ```python
  turn = await self.memory.aappend_turn(
      session_id=session_id,
      user_query=request.question,
      assistant_answer=domain_output.answer,
      rewind_turn_index=getattr(request, "rewind_turn_index", None),
      attachments=getattr(request, "attachments", None) or [],
      citations=domain_output.citations,
      source_used=domain_output.source_used,
      trace_id=tid,
  )
  ```
- Remove all calls to `self.memory.atruncate_from` in `runner.py`.

### 2.2 Refactor `WorkflowRunner._ahydrate_memory` (`app/rag/workflows/runner.py`)
- Collapse the branched hydration logic:
  ```python
  rewind_turn_index = getattr(request, "rewind_turn_index", None)
  max_turns = getattr(self.settings, "memory_max_turns", 10)

  hydrated = await self.memory.aget_messages(
      session_id,
      limit=max_turns,
      before_turn_index=rewind_turn_index,
  )
  ```
- Remove full-session `await self.memory.aget_turns(session_id)` and manual list slicing during rewinds.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- In `tests/rag/test_workflow_runner_memory.py`:
  - Update mock memory doubles to implement `aappend_turn`.
  - Verify `_apersist_turn` calls `aappend_turn` with correct scalar parameters and returns the persisted turn.
  - Verify `_ahydrate_memory` passes `before_turn_index=rewind_turn_index` to `aget_messages`.
  - Verify that `WorkflowRunner` never calls `atruncate_from` or `aget_turns` during normal or rewind execution.

### Green (Implementation)
- Implement refactoring in `app/rag/workflows/runner.py`.

---

## Acceptance Criteria
- [ ] `WorkflowRunner._apersist_turn` delegates strictly to `await self.memory.aappend_turn(...)`.
- [ ] `WorkflowRunner._ahydrate_memory` uses a single `aget_messages` call with `before_turn_index`.
- [ ] Direct calls to `atruncate_from` and `aget_turns` are eliminated from `WorkflowRunner`.
- [ ] All tests in `tests/rag/test_workflow_runner_memory.py` pass cleanly.
