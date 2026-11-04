# Technical Specification: Deepen Conversation Memory with Session Aggregate and Atomic Branching Seam

- **ADR Reference:** `docs/adr/0023-deepen-conversation-memory-session-aggregate.md`
- **Target Subsystems:** `app/rag/memory/`, `app/api/`, `tests/rag/`, `tests/api/`
- **Specification Document:** `docs/spec.md` & `docs/specs/0023-deepen-conversation-memory-session-aggregate-spec.md`
- **Status:** Proposed / Ready for Review
- **Triage Label:** `ready-for-agent`

---

## Problem Statement

In ADR 0016 and ADR 0017, the `ConversationMemoryAdapter` protocol seam was introduced and deepened on the write path (`aappend_turn`) to provide atomic turn persistence and bounded message queries. However, the read-path and session-branching architecture remained shallow, fragmented, and leaky:

1. **Disjointed Domain Models & Missing Session Aggregate**:
   `app/rag/memory/base.py` defined `SessionInfo` (metadata only) and `ConversationTurn` (turn only). There was no domain entity representing an entire conversation session. As a result, domain logic was unable to treat a session as a coherent aggregate entity.
2. **Multi-Query Read Friction in Callers**:
   To retrieve a full conversation session (e.g. `GET /api/chat/{session_id}`), callers were forced to make two distinct asynchronous database round trips: first `aget_session(session_id)` to get metadata, then `aget_turns(session_id)` to get turn history. The caller was then responsible for manually stitching these disparate entities together.
3. **Fragmented 4-Step Session Branching (`POST /api/chat/{session_id}/fork`)**:
   `ConversationMemoryAdapter.afork_session` returned only a raw string `new_session_id`. Consequently, the HTTP presentation route in `app/api/routes.py` was forced to execute a 4-step orchestration:
   - Pre-check source session existence via `await memory.aget_session(session_id)`
   - Execute fork via `await memory.afork_session(session_id, ...)`
   - Re-query child metadata via `await memory.aget_session(new_session_id)`
   - Query child turns via `await memory.aget_turns(new_session_id)`
   - Manually map disjointed records into `SessionDetailResponse.from_domain(new_session, new_turns)`
   This created unnecessary network/database latency, increased transaction isolation hazards, and bloated the presentation layer.
4. **Inconsistent Invariant Enforcement Across Adapters**:
   When forking a non-existent parent session, `SQLiteConversationStore` threw a raw `sqlite3.IntegrityError` due to foreign keys, while `InMemoryConversationStore` silently created an orphaned fork (`Fork of <missing-id>`). Callers could not rely on consistent domain exception semantics.
5. **Presentation Layer Stitching & Transport Coupling**:
   `SessionDetailResponse.from_domain` in `app/api/schemas.py` required two separate arguments `(info: SessionInfo, turns: list[ConversationTurn])`, exposing the underlying database split to transport serializers.

---

## Solution

Deepen the `ConversationMemoryAdapter` protocol seam and concrete adapters (`SQLiteConversationStore`, `InMemoryConversationStore`, `NoOpMemoryStore`) by introducing a cohesive `ConversationSession` aggregate, making session retrieval and branching atomic 1-call operations:

1. **`ConversationSession` Domain Aggregate (`app/rag/memory/base.py`)**:
   Introduce `ConversationSession` subtyping `SessionInfo`:
   ```python
   class ConversationSession(SessionInfo):
       """Canonical domain aggregate representing a complete conversation session with turns."""

       turns: list[ConversationTurn] = Field(default_factory=list)
   ```
   Subtyping `SessionInfo` guarantees 100% backward compatibility with any consumer reading metadata properties (`session.session_id`, `session.title`, `session.turn_count`, `session.created_at`, `session.updated_at`, `session.parent_session_id`, `session.forked_from_turn`).
2. **Deepen Session Retrieval Seam (`aget_session`)**:
   Deepen `ConversationMemoryAdapter.aget_session(session_id: str, limit: int | None = None) -> ConversationSession | None`:
   - In `SQLiteConversationStore`: Fetch both session metadata and turns within a single database transaction. If `limit` is specified, query the latest $N$ turns via `ORDER BY turn_index DESC LIMIT ?` and reverse the sequence in Python to guarantee that `session.turns` is always sorted chronologically ascending (`turn_index ASC`). If `limit is None`, fetch directly `ORDER BY turn_index ASC`.
   - In `InMemoryConversationStore`: Return a deep copy of `ConversationSession` with its turn slice ordered chronologically.
   - In `NoOpMemoryStore`: Return `None`.
3. **Atomic Session Branching Seam (`afork_session`)**:
   Deepen `ConversationMemoryAdapter.afork_session(source_session_id: str, upto_turn_index: int, new_session_id: str | None = None) -> ConversationSession`:
   - **Domain Invariants**: Both SQLite and in-memory stores validate preconditions:
     - If `source_session_id` does not exist: raise `KeyError(f"Session not found: {source_session_id}")`.
     - If `upto_turn_index < -1`: raise `ValueError(f"Invalid upto_turn_index: {upto_turn_index}. Must be >= -1.")`.
   - **Zero Additional I/O**: The adapter constructs and returns the newly cloned `ConversationSession` directly from the active write transaction without subsequent read queries.
   - **Ephemeral Return on `NoOpMemoryStore`**: Returns an ephemeral `ConversationSession(session_id=new_session_id or uuid.uuid4().hex, turns=[], ...)` ensuring callers never handle `None`.
4. **Streamline Presentation Serialization & HTTP Routes (`app/api/`)**:
   - Update `SessionDetailResponse.from_domain` to accept `session: ConversationSession | SessionInfo, turns: list[ConversationTurn] | None = None`. If `session` is `ConversationSession`, it extracts `session.turns`; if `session` is `SessionInfo`, it uses `turns`.
   - Collapse `GET /api/chat/{session_id}` into 1 query:
     ```python
     session = await memory.aget_session(session_id)
     if not session:
         raise HTTPException(status_code=404, detail="Session not found")
     return SessionDetailResponse.from_domain(session)
     ```
   - Collapse `POST /api/chat/{session_id}/fork` into 1 atomic call:
     ```python
     try:
         new_session = await memory.afork_session(session_id, payload.upto_turn_index, payload.new_session_id)
         return SessionDetailResponse.from_domain(new_session)
     except KeyError as exc:
         raise HTTPException(status_code=404, detail=str(exc)) from exc
     except ValueError as exc:
         raise HTTPException(status_code=400, detail=str(exc)) from exc
     ```
5. **Preserve Low-Level Seams**:
   Retain `aget_turns`, `asave_turn`, `atruncate_from`, `aget_messages`, `alist_sessions`, and `aclear` on `ConversationMemoryAdapter` for test fixture seeding, explicit truncation endpoints, and LLM context binding in `ExecutionContextBinder`.

---

## User Stories

1. As an API client fetching session history via `GET /api/chat/{session_id}`, I want the endpoint to resolve my session metadata and turns in a single atomic database query, so that my chat UI hydrates with minimal latency and zero race conditions.
2. As an API client branching a conversation via `POST /api/chat/{session_id}/fork`, I want the endpoint to clone turns and return the complete new session aggregate in one call, so that I immediately receive the forked session details without delay.
3. As an API client attempting to fork a non-existent session, I want to receive an HTTP 404 response with a clear error message, so that I can notify the user that the parent conversation is missing.
4. As an API client providing an invalid `upto_turn_index` (e.g. `< -1`), I want to receive an HTTP 400 response with descriptive validation details, so that client bugs are detected immediately.
5. As a developer writing multi-turn conversational agents, I want `ConversationSession` to be a first-class domain model subtyping `SessionInfo`, so that I can pass a full session object across layers without losing metadata.
6. As a developer using `ConversationSession`, I want `session.turns` to always be sorted in ascending chronological order (`turn_index ASC`), so that messages read naturally from oldest to newest.
7. As a developer querying a session with `limit=N`, I want to receive the most recent $N$ turns in ascending chronological order, so that long conversations show the latest turns without requiring client-side inversion.
8. As a backend engineer, I want `SQLiteConversationStore.aget_session` to query session metadata and turns in a single transaction, so that SQLite connections are released immediately.
9. As a backend engineer, I want `SQLiteConversationStore.afork_session` to raise `KeyError` when the parent session does not exist, so that foreign key errors are translated into clean domain exceptions.
10. As a test author, I want `InMemoryConversationStore.afork_session` to enforce the same `KeyError` and `ValueError` invariants as SQLite, so that unit tests catch domain violations identically to production.
11. As a test author, I want `InMemoryConversationStore.afork_session` to return a complete `ConversationSession` aggregate, so that in-memory tests never require follow-up reads or manual mock wiring.
12. As a platform developer running in stateless mode (`NoOpMemoryStore`), I want `afork_session` to return an ephemeral `ConversationSession` object with an empty turns list, so that callers never encounter `None` or attribute errors.
13. As a presentation layer engineer, I want `SessionDetailResponse.from_domain` to accept a single `ConversationSession` parameter, so that route handlers avoid separate `turns` parameters.
14. As a test author maintaining legacy test suites, I want `SessionDetailResponse.from_domain(info, turns)` to remain supported polymorphically, so that existing unit tests continue to pass without churn.
15. As an IT support assistant user, I want session branching at Turn 0 (`upto_turn_index = -1`) to create an empty child session linked to the parent, so that I can explore fresh inquiries with traceable lineage.
16. As a database maintainer, I want child sessions created via `afork_session` to automatically compute their title as `{source_title} (Fork)` and maintain correct `turn_count`, so that session summaries in `GET /api/chat/sessions` accurately reflect the branch.

---

## Implementation Decisions

### Domain Models & Protocol Seam
- **Module**: `app/rag/memory/base.py`
- **Interface Modifications**:
  - Define `ConversationSession(SessionInfo)` with `turns: list[ConversationTurn] = Field(default_factory=list)`.
  - Update `ConversationMemoryAdapter.aget_session`:
    ```python
    async def aget_session(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> ConversationSession | None:
        """Retrieve full conversation session aggregate with chronological turn history."""
        ...
    ```
  - Update `ConversationMemoryAdapter.afork_session`:
    ```python
    async def afork_session(
        self,
        source_session_id: str,
        upto_turn_index: int,
        new_session_id: str | None = None,
    ) -> ConversationSession:
        """Clone turns [0 .. upto_turn_index] into a child session and return the complete aggregate."""
        ...
    ```
  - Retain all other existing protocol methods: `aget_messages`, `aget_turns`, `aappend_turn`, `asave_turn`, `aclear`, `atruncate_from`, `alist_sessions`.

### Concrete Memory Adapters
- **Module**: `app/rag/memory/sqlite.py` (`SQLiteConversationStore`)
  - In `aget_session(session_id, limit=None)`:
    - Execute read in `run_in_threadpool` using `self._connection()`.
    - Fetch row from `conversation_sessions`. If `None`, return `None`.
    - If `limit` is specified: query `SELECT ... FROM conversation_turns WHERE session_id = ? ORDER BY turn_index DESC LIMIT ?`, then reverse rows.
    - If `limit is None`: query `SELECT ... FROM conversation_turns WHERE session_id = ? ORDER BY turn_index ASC`.
    - Deserialize JSON columns (`attachments_json`, `citations_json`) into `ConversationTurn` objects.
    - Compute `turn_count` from row or actual turns.
    - Return `ConversationSession(**session_data, turns=turns)`.
  - In `afork_session(source_session_id, upto_turn_index, new_session_id=None)`:
    - Validate `upto_turn_index >= -1`; if not, raise `ValueError`.
    - Within `self._write_lock` and `with conn:`:
      - Query source session row. If not found, raise `KeyError(f"Session not found: {source_session_id}")`.
      - Generate `target_id = new_session_id or uuid.uuid4().hex`.
      - Insert into `conversation_sessions`.
      - If `upto_turn_index >= 0`: insert cloned turns into `conversation_turns` using `SELECT ... WHERE session_id = ? AND turn_index <= ? ORDER BY turn_index ASC`.
      - Query cloned turns for `target_id` and construct `ConversationSession`.
      - Return `ConversationSession`.
- **Module**: `app/rag/memory/in_memory.py` (`InMemoryConversationStore`)
  - In `aget_session(session_id, limit=None)`:
    - Acquire `self._lock`.
    - Check `self._sessions.get(session_id)`. If `None`, return `None`.
    - Retrieve turns from `self._turns.get(session_id, [])`.
    - If `limit` is specified: take `turns[-limit:]`.
    - Return deep copy of `ConversationSession(**session.model_dump(), turns=slice)`.
  - In `afork_session(source_session_id, upto_turn_index, new_session_id=None)`:
    - Validate `upto_turn_index >= -1`; if not, raise `ValueError`.
    - Acquire `self._lock`.
    - Check `self._sessions.get(source_session_id)`. If `None`, raise `KeyError(f"Session not found: {source_session_id}")`.
    - Clone turns with updated `session_id = target_id`.
    - Persist `SessionInfo` in `self._sessions` and cloned turns in `self._turns`.
    - Return `ConversationSession(**session_info.model_dump(), turns=cloned_turns)`.
- **Module**: `app/rag/memory/noop.py` (`NoOpMemoryStore`)
  - `aget_session`: returns `None`.
  - `afork_session`: returns ephemeral `ConversationSession(session_id=new_session_id or uuid.uuid4().hex, title="Ephemeral Fork", turns=[])`.

### Presentation Layer Schemas & Routes
- **Module**: `app/api/schemas.py`
  - Update `SessionDetailResponse.from_domain`:
    ```python
    @classmethod
    def from_domain(
        cls,
        session: ConversationSession | SessionInfo,
        turns: list[ConversationTurn] | None = None,
    ) -> SessionDetailResponse:
        resolved_turns = session.turns if isinstance(session, ConversationSession) else (turns or [])
        return cls(
            session_id=session.session_id,
            title=session.title,
            parent_session_id=session.parent_session_id,
            forked_from_turn=session.forked_from_turn,
            created_at=session.created_at,
            updated_at=session.updated_at,
            turn_count=session.turn_count,
            turns=[ChatTurnResponse.from_domain(t) for t in resolved_turns],
        )
    ```
- **Module**: `app/api/routes.py`
  - `get_chat_session(session_id: str)`:
    - Call `session = await memory.aget_session(session_id)`.
    - If `session is None`, raise `HTTPException(status_code=404, detail="Session not found")`.
    - Return `SessionDetailResponse.from_domain(session)`.
  - `fork_chat_session(session_id: str, payload: ForkRequest)`:
    - Wrap in `try ... except KeyError ... except ValueError`:
      ```python
      try:
          new_session = await memory.afork_session(session_id, payload.upto_turn_index, payload.new_session_id)
          return SessionDetailResponse.from_domain(new_session)
      except KeyError as exc:
          raise HTTPException(status_code=404, detail=str(exc)) from exc
      except ValueError as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from exc
      ```

---

## Testing Decisions

### What Makes a Good Test
- **Test External Behavior, Not Implementation**: Tests must verify that calling `aget_session` returns a `ConversationSession` with correct metadata and chronological turns, and that `afork_session` creates the cloned session with lineage and returns the aggregate.
- **Invariant Assertions**: Verify that missing parent sessions raise `KeyError` across both SQLite and In-Memory stores, and invalid `upto_turn_index < -1` raises `ValueError`.
- **Chronological Order Under Limit**: Verify that querying `aget_session(session_id, limit=2)` on a session with 5 turns returns the last 2 turns in chronological ascending order (`[Turn 3, Turn 4]`).
- **Zero Mock Presentation Tests**: Verify `GET /api/chat/{session_id}` and `POST /api/chat/{session_id}/fork` via `TestClient` or ASGI client, asserting HTTP 200, 400, and 404 status codes and JSON payloads matching `SessionDetailResponse`.

### Modules Tested
- `tests/rag/test_conversation_memory_base.py`: Protocol conformance and `ConversationSession` inheritance.
- `tests/rag/test_conversation_memory_sqlite.py`: `SQLiteConversationStore` implementation of `aget_session`, `afork_session`, limit tail-windowing, and `KeyError`/`ValueError` exceptions.
- `tests/rag/test_conversation_memory_in_memory.py`: `InMemoryConversationStore` aggregate return, turn slicing, and error invariants.
- `tests/api/test_routes_chat_memory.py`: HTTP routes for single-query session detail and atomic fork responses.

### Prior Art
- `tests/rag/test_conversation_memory_sqlite.py` (ADR-0016 / ADR-0017 suite)
- `tests/rag/test_conversation_memory_in_memory.py`
- `tests/api/test_routes_chat_memory.py`

---

## Out of Scope

- Modifying the write path (`aappend_turn`) or pre-rewind message queries (`before_turn_index`), which were finalized in ADR-0017.
- Changing `ExecutionContextBinder._ahydrate_messages`, which continues to use bounded `aget_messages(limit, before_turn_index)`.
- Introducing distributed caching or Redis adapters for session caching.
- Modifying frontend UI components or client-side TypeScript types.

---

## Further Notes

- By making `ConversationSession` inherit from `SessionInfo`, all existing code reading `session.session_id`, `session.title`, or `session.turn_count` remains 100% compatible.
- The return type change of `afork_session` from `str` to `ConversationSession` improves caller locality: callers wanting the session ID simply access `result.session_id`.
