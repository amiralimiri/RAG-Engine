# Conversation Memory & Session Branching Seam Patterns

Guidelines and architecture patterns from ADR-0016 (Unified Pluggable Conversation Memory and Session Branching) for managing multi-turn conversation persistence, session resumption, atomic rewind, and conversation branching across all workflows.

---

## 1. Core Principles

1. **Protocol Decoupling from Graph Checkpointers**:
   - The memory seam (`ConversationMemoryAdapter` in `app/rag/memory/base.py`) is a standalone domain protocol, completely independent from LangGraph's internal `BaseCheckpointSaver`.
   - Never serialize raw LangGraph checkpoint blobs into the conversation memory store. Persist strictly canonical `(HumanMessage, AIMessage)` pairs packaged as `ConversationTurn` domain models.

2. **Server-Side Persistence as Single Source of Truth**:
   - When `session_id` is provided and memory is enabled, `WorkflowRunner._prepare_execution` hydrates conversation history from the server-side store via `memory.aget_messages(session_id, limit=max_turns)`.
   - Client-provided `messages` in `WorkflowInput` serve strictly as an initialization fallback when `session_id` is absent or the store is empty.

3. **Atomic Post-Execution Persistence with Safe Truncation**:
   - Turns are persisted atomically in `WorkflowRunner._finalize_execution` **only** after generation succeeds (in both batch `execute` and streaming `stream`).
   - If an unhandled exception or client disconnect occurs, turn persistence is aborted to avoid storing partial or corrupted states.
   - For atomic edit-and-run operations (`rewind_turn_index = k`), `_prepare_execution` loads history `[0 .. k-1]` without deleting subsequent turns. Truncation (`atruncate_from(session_id, k)`) is committed together with the new turn persistence **only** upon successful completion.

4. **Transient State Scrubbing in Multi-Turn DAGs**:
   - `AgentState` and `StandardWorkflowState` include `messages: Annotated[list[BaseMessage], operator.add]`.
   - On each new turn, `to_initial_state` strictly scrubs all transient retrieval and grading artifacts (`kb_docs`, `web_results`, `retry_count`, `citations`, `retrieved_contexts`, `trace`), resetting them to empty defaults so Turn 1 artifacts never bleed into Turn 2.

5. **Conversational Query Condensation**:
   - In `SelfCorrectiveRagWorkflow` (`create_agentic_rag_graph`), a conditional edge after `START` routes queries to `condense_query` when `len(messages) > 1`.
   - `condense_query` resolves pronouns and references across prior messages into a standalone query, writing to `state["current_query"]` while preserving `state["question"]` verbatim.
   - When `len(messages) <= 1`, condensation is bypassed with 0ms latency overhead, and `current_query` defaults to `question`.

6. **Relational SQLite Schema with WAL & Lineage Tracking**:
   - The default persistent store `SQLiteConversationStore` operates on two tables: `conversation_sessions` and `conversation_turns`.
   - Enforce `PRAGMA foreign_keys=ON;`, `PRAGMA journal_mode=WAL;`, and `PRAGMA busy_timeout=5000;`.
   - Dispatch synchronous `sqlite3` operations via Starlette's `run_in_threadpool` and an async lock to keep the event loop responsive.
   - Support conversation branching (`afork_session`) with `parent_session_id` and `forked_from_turn`, including `upto_turn_index = -1` for Turn 0 branching.

---

## 2. Seam Protocol Contract (`app/rag/memory/base.py`)

```python
class ConversationTurn(BaseModel):
    session_id: str
    turn_index: int
    user_query: str
    assistant_answer: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    source_used: str = "direct"
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class SessionInfo(BaseModel):
    session_id: str
    title: str
    parent_session_id: str | None = None
    forked_from_turn: int | None = None
    created_at: datetime
    updated_at: datetime
    turn_count: int = 0

@runtime_checkable
class ConversationMemoryAdapter(Protocol):
    async def aget_messages(self, session_id: str, limit: int | None = None) -> list[BaseMessage]: ...
    async def aget_turns(self, session_id: str, limit: int | None = None) -> list[ConversationTurn]: ...
    async def asave_turn(self, turn: ConversationTurn) -> None: ...
    async def aclear(self, session_id: str) -> None: ...
    async def atruncate_from(self, session_id: str, turn_index: int) -> None: ...
    async def afork_session(self, source_session_id: str, upto_turn_index: int, new_session_id: str | None = None) -> str: ...
    async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]: ...
    async def aget_session(self, session_id: str) -> SessionInfo | None: ...
```

---

## 3. Test Doubles & Fixtures

When authoring unit and integration tests:
- Use `InMemoryConversationStore` (available in `app/rag/memory/in_memory.py`) as the test double:
  ```python
  @pytest.fixture
  def memory_store() -> InMemoryConversationStore:
      return InMemoryConversationStore()
  ```
- Inject `memory_store` into `WorkflowRunner`:
  ```python
  runner = WorkflowRunner(registry=registry, memory=memory_store)
  ```
- Never rely on live SQLite database files in unit tests. Use `InMemoryConversationStore` or temporary paths (`tmp_path / "test.db"`).
