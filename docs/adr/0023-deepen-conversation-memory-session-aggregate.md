# ADR 0023: Deepen Conversation Memory with Session Aggregate and Atomic Branching Seam

## Status
Accepted (Extends ADR 0016 and ADR 0017)

## Context
In ADR 0016, we introduced the `ConversationMemoryAdapter` protocol seam with concrete implementations (`SQLiteConversationStore`, `InMemoryConversationStore`, `NoOpMemoryStore`) to manage multi-turn conversational history and session branching. In ADR 0017, we deepened the write path (`aappend_turn`) to be atomic and transactional, and bounded pre-rewind message queries (`before_turn_index`).

However, the read and branching paths of `ConversationMemoryAdapter` remained fragmented and shallow:
1. **Disjointed Domain Models**: `SessionInfo` (metadata only) and `ConversationTurn` (turn only) forced callers to treat a session as disconnected pieces. There was no domain aggregate representing a complete conversation session with its history.
2. **Multi-Query Read Friction**: To retrieve a full session (e.g. `GET /api/chat/{session_id}`), callers were forced to make two separate asynchronous queries: `aget_session` to get metadata, followed by `aget_turns` to get turns, and then manually stitch them together.
3. **Fragmented 4-Step Session Branching**: `afork_session` only returned a bare string (`new_session_id`). In `app/api/routes.py`, `POST /api/chat/{session_id}/fork` had to issue 4 separate round trips: pre-check `aget_session(session_id)`, invoke `afork_session`, follow up with `aget_session(new_session_id)`, and then `aget_turns(new_session_id)`.
4. **Inconsistent Error and Invariant Enforcement**: Missing parent sessions caused `sqlite3.IntegrityError` in SQLite, silent orphan session creation in in-memory, and manual 404 pre-checks in the HTTP route.
5. **Presentation Layer Stitching**: `SessionDetailResponse.from_domain` required two separate arguments `(info, turns)`, exposing storage separation to transport serializers.

To achieve clean module depth, caller leverage, and storage locality, the `ConversationMemoryAdapter` protocol seam must encapsulate session aggregation, atomic fork return, and centralized domain invariant validation.

---

## Decisions

### 1. `ConversationSession` Domain Aggregate (`app/rag/memory/base.py`)
Introduce a strongly-typed domain aggregate model in `app/rag/memory/base.py` that inherits directly from `SessionInfo`:
```python
class ConversationSession(SessionInfo):
    """Canonical domain aggregate representing a complete conversation session with turns."""

    turns: list[ConversationTurn] = Field(default_factory=list)
```
- Subtyping `SessionInfo` ensures 100% backward compatibility wherever `SessionInfo` attributes (`session_id`, `title`, `turn_count`, `created_at`, `updated_at`, `parent_session_id`, `forked_from_turn`) are accessed.
- Encapsulates the entire session state inside a single coherent domain entity.

### 2. Deepen Session Retrieval Seam (`aget_session`)
Upgrade `aget_session` on `ConversationMemoryAdapter` to return the complete `ConversationSession` aggregate:
```python
async def aget_session(
    self,
    session_id: str,
    limit: int | None = None,
) -> ConversationSession | None:
    """Retrieve full conversation session aggregate with chronological turn history."""
    ...
```
- **Single Database Transaction**: Both metadata and turns are retrieved within a single query operation.
- **Tail-Window Semantics**: When `limit` is specified, the adapter queries the latest $N$ turns (`ORDER BY turn_index DESC LIMIT ?`) and reverses the result in Python so that `session.turns` is always sorted in ascending chronological order (`turn_index ASC`). When `limit is None`, all turns are returned in natural ascending order.
- If the session does not exist, returns `None`.

### 3. Atomic Session Branching Seam (`afork_session`)
Deepen `afork_session` to return the newly created `ConversationSession` aggregate directly from the write transaction:
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
- **Domain Invariant Enforcement**: Both `SQLiteConversationStore` and `InMemoryConversationStore` validate preconditions:
  - If `source_session_id` does not exist: raise `KeyError(f"Session not found: {source_session_id}")`.
  - If `upto_turn_index < -1`: raise `ValueError(f"Invalid upto_turn_index: {upto_turn_index}. Must be >= -1.")`.
- **Zero Additional I/O**: The adapter already holds the parent metadata and cloned turn records in scope during the fork transaction; it constructs and returns `ConversationSession` immediately.
- **Ephemeral Return on `NoOpMemoryStore`**: Constructs and returns an ephemeral `ConversationSession(session_id=new_session_id or uuid.uuid4().hex, turns=[], ...)` ensuring callers never face `None`.

### 4. Streamlined Presentation Route and Serializer (`app/api/`)
- In `app/api/schemas.py`, modernize `SessionDetailResponse.from_domain` to accept either the single aggregate or the legacy pair:
  ```python
  @classmethod
  def from_domain(
      cls,
      session: ConversationSession | SessionInfo,
      turns: list[ConversationTurn] | None = None,
  ) -> SessionDetailResponse:
      resolved_turns = session.turns if isinstance(session, ConversationSession) else (turns or [])
      ...
  ```
- In `app/api/routes.py`:
  - `GET /api/chat/{session_id}` collapses from 2 database queries to 1:
    ```python
    session = await memory.aget_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetailResponse.from_domain(session)
    ```
  - `POST /api/chat/{session_id}/fork` collapses from 4 queries to 1 atomic call:
    ```python
    try:
        new_session = await memory.afork_session(session_id, payload.upto_turn_index, payload.new_session_id)
        return SessionDetailResponse.from_domain(new_session)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ```

### 5. Seam Backward Compatibility
- Retain `aget_turns`, `asave_turn`, `atruncate_from`, `aget_messages`, `alist_sessions`, and `aclear` on `ConversationMemoryAdapter` for test fixture seeding, explicit truncation endpoints, and LLM context binding.

---

## Consequences

### Positive
- **Deep Seam**: Callers interact with a single, complete domain aggregate (`ConversationSession`) rather than orchestrating disjointed metadata and turn queries.
- **Maximum Caller Leverage**: Session retrieval drops from 2 queries to 1; session forking drops from 4 queries to 1.
- **Strict Storage Locality**: Parent existence validation and turn copying invariants live inside the adapters, eliminating defensive pre-checks across callers.
- **Zero-Shim Simplicity**: `SessionDetailResponse.from_domain(session)` works cleanly with the aggregate while preserving legacy 2-argument calls.
- **Testability**: Unit tests assert against self-contained `ConversationSession` instances with natural chronological ordering.

### Negative / Trade-Offs
- Unit tests that explicitly assert on `await memory.afork_session(...) == "child-id"` must be updated to assert on `result.session_id == "child-id"` or `isinstance(result, ConversationSession)`.
