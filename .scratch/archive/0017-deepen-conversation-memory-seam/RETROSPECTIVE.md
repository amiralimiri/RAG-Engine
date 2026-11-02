# Retrospective: Milestone 0017 / Candidate 1 (Deepen Conversation Memory Seam)

**Date:** November 2, 2026  
**Status:** Completed, Reviewed, Formatted, & Archived (Candidate 1 Concluded)  
**ADR Reference:** `docs/adr/0017-deepen-conversation-memory-seam.md`  
**Specification:** `docs/spec.md` & `docs/specs/0017-deepen-conversation-memory-seam-spec.md`  
**Work Packages / Tickets:** Tickets 01–05 (Archived under `.scratch/archive/0017-deepen-conversation-memory-seam/issues/`)  
**Domain Guidelines:** `docs/agents/conversation-memory.md` | `docs/agents/codebase-unification.md` | `CODING_STANDARDS.md`  
**Test Suite:** 678 passed, 0 failed, 100% offline-capable, clean ruff check & format across workspace  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0017 successfully implemented **Candidate 1** from the architectural design review, deepening the `ConversationMemoryAdapter` protocol seam to encapsulate atomic turn appending, transactional monotonic index generation, atomic rewind-and-commit, and bounded message hydration behind a high-leverage interface.

Prior to ADR-0017, the memory seam leaked internal storage math onto callers (`WorkflowRunner`), requiring $O(N)$ full-session JSON deserialization in SQLite on every chat turn, exposing concurrency race hazards on index calculation, and risking data corruption from non-atomic two-phase rewinds.

With this release:
1. Turn appending is atomic, transactional, and encapsulated in `aappend_turn`.
2. Turn indexing is resolved directly in SQL (`COALESCE(MAX(turn_index) + 1, 0)`) in $O(1)$ without JSON deserialization.
3. Rewind deletion and replacement turn insertion execute within a single atomic SQLite transaction (`with conn:`) with guaranteed rollback.
4. Pre-rewind message hydration pushes `AND turn_index < before_turn_index` filters directly into SQL queries, eliminating Python-side turn slicing.
5. `WorkflowRunner` caller complexity is streamlined, completely eliminating manual turn counting, index arithmetic, and direct runner calls to `atruncate_from` and `aget_turns`.

### Key Deliverables:

1. **Protocol Seam Deepening & No-Op Adapter (Ticket 01)**:
   - Added `aappend_turn` to `ConversationMemoryAdapter` in `app/rag/memory/base.py` accepting scalar parameters and returning the persisted `ConversationTurn`.
   - Extended `aget_messages` and `aget_turns` with `before_turn_index: int | None = None`.
   - Implemented `NoOpMemoryStore.aappend_turn` in `app/rag/memory/noop.py` returning ephemeral, non-null `ConversationTurn` models stamped with the expected `turn_index`.

2. **In-Memory Store Atomic Append & Bounded Slicing (Ticket 02)**:
   - Implemented `aappend_turn` and `before_turn_index` filtering in `InMemoryConversationStore` (`app/rag/memory/in_memory.py`) under thread lock (`self._lock`).
   - Verified that in-memory test doubles faithfully mirror SQLite indexing rules and rewind semantics with deep-copy isolation.

3. **SQLite Adapter Transactional Append & SQL-Level Queries (Ticket 03)**:
   - Implemented `aappend_turn` in `SQLiteConversationStore` (`app/rag/memory/sqlite.py`) executing inside a single SQLite transaction block (`with conn:`) offloaded via `run_in_threadpool` and serialized via `async with self._write_lock`.
   - Replaced caller-managed turn counts with `SELECT COALESCE(MAX(turn_index) + 1, 0) AS next_idx FROM conversation_turns WHERE session_id = ?`.
   - Integrated `before_turn_index` parameter directly into SQL `WHERE` clauses for both bounded (`LIMIT ?`) and unbounded `aget_turns` queries.

4. **WorkflowRunner Caller Streamlining & Hydration Collapse (Ticket 04)**:
   - Refactored `WorkflowRunner._apersist_turn` (`app/rag/workflows/runner.py`) into a single-line delegation to `await self.memory.aappend_turn(...)`.
   - Collapsed branched rewind slicing in `WorkflowRunner._ahydrate_memory` into a unified call: `await self.memory.aget_messages(session_id, limit=max_turns, before_turn_index=rewind_turn_index)`.
   - Treated `WorkflowInput.messages` as an immutable DTO, passing hydrated history directly into execution context (`hydrated_messages`) without in-place mutation.
   - Enforced sentinel session ID validation (`None`, `""`, `"   "`, `"undefined"`, `"null"`).

5. **End-to-End Integration & Full Regression Verification (Ticket 05)**:
   - Expanded `tests/api/test_routes_chat_memory.py` with multi-turn chat, streaming SSE rewind, and standalone REST rewind tests.
   - Verified backward compatibility for the explicit REST endpoint (`POST /api/chat/{session_id}/rewind`) using `atruncate_from`.
   - Verified all 678 tests pass with 0 failures across the test suite.

---

## 2. What Went Well

1. **High-Leverage Seam Deepening**:
   - By pushing turn indexing and rewind truncation down into the storage adapters, `WorkflowRunner` was decoupled from database mechanics. Caller code in `runner.py` became noticeably simpler, cleaner, and less error-prone.
2. **Atomic Rollback Contract Verification**:
   - Developed `test_sqlite_append_turn_rollback_on_failure` utilizing an SQLite `BEFORE INSERT` trigger to simulate runtime storage failure during turn replacement. Proved that on abort, the transaction rolls back completely and existing turns remain intact.
3. **Turn 0 Rewind Boundary Correctness**:
   - Explicitly handled the Turn 0 rewind edge case where `hydrated == []` by checking `if hydrated or rewind_turn_index is not None: return hydrated`, preventing fallback to stale `request.messages`.
4. **Zero-Shim Backward Compatibility**:
   - Retained `asave_turn` for fixture seeding and `atruncate_from` for REST transport endpoints without cluttering the primary execution pipeline with compatibility shims.

---

## 3. Friction Points & Lessons Learned

1. **Commit Hygiene & Formatting Isolation**:
   - *Issue*: Commit `04df4af` combined substantive SQLite implementation work with a broad workspace formatting pass across 75 files (including Jupyter notebooks and unrelated tests).
   - *Lesson*: Formatting passes should always be isolated into separate commits (`style: format workspace`) before or after feature commits to keep git diffs reviewable and avoid polluting `git blame`.
2. **Trailing Newline Formatter Warning**:
   - *Issue*: `tests/rag/test_workflow_runner_memory.py` was committed with an extra blank line at the EOF, caught during code review by `ruff format --check .`.
   - *Lesson*: Always run `uv run ruff format --check .` and `uv run ruff check .` as an automated pre-review or pre-commit verification gate.

---

## 4. Codified Agent Guardrails

The following guardrails are reinforced from this milestone:

1. **Encapsulate Storage Mechanics Behind Protocol Seams**:
   - Storage adapters must own index generation, serialization, and transaction boundaries. Callers (`WorkflowRunner`) must never compute row indices or orchestrate multi-step mutations that should be atomic.
2. **Immutable Input DTOs**:
   - Domain request objects (`WorkflowInput`) must remain strictly immutable throughout execution. Derived states and hydrated history should be threaded down via execution context (`hydrated_messages`), never written onto input objects in-place.
3. **Atomic Rollback by Default**:
   - Any operation that deletes historical data and replaces it (such as turn rewind) must occur in a single atomic transaction block (`with conn:`). Never split deletion and insertion into separate asynchronous database queries.

---

## 5. Artifact & Ticket Archive Status

All 5 tickets for Milestone 0017 are completed and archived under `.scratch/archive/0017-deepen-conversation-memory-seam/issues/`:

| Ticket | Description | Status |
|---|---|---|
| **Ticket 01** | Protocol Seam Deepening & No-Op Adapter | Completed & Archived |
| **Ticket 02** | In-Memory Adapter Atomic Append & Bounded Slicing | Completed & Archived |
| **Ticket 03** | SQLite Adapter Transactional Append & SQL-Level Queries | Completed & Archived |
| **Ticket 04** | WorkflowRunner Caller Streamlining & Hydration Collapse | Completed & Archived |
| **Ticket 05** | End-to-End Integration & Regression Verification | Completed & Archived |

Active scratch directory `.scratch/0017-deepen-conversation-memory-seam` has been cleared and merged into the archive.

---

## 6. Candidate 1 Concluded & Next Steps (Candidate 2)

With the completion and sign-off of Milestone 0017, **Candidate 1 is officially concluded**.

### Next Milestone Preview: Candidate 2
- **Objective:** Streamline `WorkflowRunner` execution pipeline, streaming generator (`astream`), and execution profile resolution.
- **Focus Areas:**
  1. Unify sync/async workflow dispatching paths.
  2. Simplify execution metadata packaging and token cost accounting hooks.
  3. Defer or implement rolling conversation memory summarization/compaction for long sessions.
