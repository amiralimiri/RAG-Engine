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

---

## 4. Safe Rewind & Atomic Truncation Pattern

1. **Defer Truncation Until Generation Succeeds**:
   - Never pre-emptively truncate or delete historical turns upon receiving an edit/rewind request (`rewind_turn_index = k`).
   - If the subsequent LLM generation, tool execution, or network connection fails/disconnects, the original conversation turns must remain intact.
   - During `_prepare_execution`, load bounded history strictly up to `[0 .. k-1]` for prompt compilation, keeping existing turns in the store untouched.
   - Truncation (`memory.atruncate_from(session_id, k)`) and persistence of the new replacement turn (`memory.asave_turn(...)`) are committed together in `_finalize_execution` **only** after generation completes without error.

2. **Session Branching (`afork_session`)**:
   - Creating an alternate lineage via session branching copies turns `0 .. upto_turn_index` from the source session to a new session ID.
   - When branching at Turn 0 before any turns exist, `upto_turn_index = -1` creates an empty branch session recording `parent_session_id` and `forked_from_turn = 0`.

---

## 5. Session Identity Sanitization & Sentinels

1. **Transport Boundary Sanitization**:
   - Never allow sentinel string literals like `"undefined"`, `"null"`, `""`, or whitespace strings to be accepted as valid `session_id` values.
   - The transport layer (`ChatRequest.sanitize_session_id`) must strip whitespace and normalize sentinel strings to `None`:
     ```python
     @field_validator("session_id", mode="before")
     @classmethod
     def sanitize_session_id(cls, v: Any) -> str | None:
         if v is None:
             return None
         if isinstance(v, str):
             v_str = v.strip()
             if not v_str or v_str.lower() in ("undefined", "null", '""', "''"):
                 return None
             return v_str
         return str(v)
     ```
   - In `WorkflowRunner._ahydrate_memory` and `_apersist_turn`, verify that `session_id` is non-empty and not a sentinel before querying or persisting turns.

2. **Client-Side Storage Normalization**:
   - Client scripts (`app.js`) must validate `localStorage` values on startup: if `localStorage.getItem("rag_current_session")` is `"undefined"` or `"null"`, purge the key and generate a pristine UUID.

---

## 6. Immutable Data Flow & Checkpointer Isolation

1. **Immutable Domain State Flow**:
   - Do not mutate caller-provided domain DTOs (`WorkflowInput.messages`) in-place during pipeline preparation or hydration.
   - When hydrated messages are retrieved from `ConversationMemoryAdapter`, derive initial graph state explicitly (`effective_messages`) without corrupting caller request objects.
   - In-place mutation risks duplicate history injections when workflows wrap internal checkpointers or re-execute.

2. **Checkpointer vs. Protocol Seam Isolation**:
   - Workflows with internal checkpointers (e.g., LangGraph `MemorySaver`) manage their own internal turn graph.
   - When a workflow specifies a checkpointer (`wf.checkpointer is not None`), `to_initial_state` must supply only the new user question (`[HumanMessage(question)]`), avoiding double-injection of historical messages already stored in the checkpointer.
   - For checkpointer-free workflows (the standard zero-shim design), `to_initial_state` accepts historical messages hydrated from the authoritative `ConversationMemoryAdapter`.

---

## 7. Frontend DOM Execution, Event Delegation & Cache Control

1. **Explicit DOM Mount Anchors**:
   - Always declare explicit container elements in `templates/index.html` (e.g. `<div id="turnActionContainer"></div>`) for dynamic controls like turn edit/rewind banners.
   - Never rely on relative DOM walking or `form.parentNode.insertBefore(banner, form)` which breaks depending on CSS layouts or dynamic card visibility.

2. **DOMPurify Whitelist for Event Delegation**:
   - Interactive components rendered via HTML templates or Markdown require custom `data-*` attributes for event delegation (`data-turn-index`, `data-query`, `data-session-id`, `data-trace-id`).
   - Ensure `PURIFY_CONFIG.ADD_ATTR` explicitly includes all custom `data-*` attributes used by click handlers. If omitted, DOMPurify silently strips the attributes, breaking event handlers in real browsers while passing headless tests.

3. **Static Asset Cache-Busting**:
   - Reference static assets in Jinja templates with explicit cache-busting version query strings (e.g. `<script src="/static/js/app.js?v=mem1"></script>`).
   - Browsers aggressively cache `.js` and `.css` files; cache-busting ensures client scripts and styles reflect recent seam and UI updates immediately.

4. **Resilient Bootstrap State**:
   - Client bootstrap functions must check `document.readyState` (`complete` or `interactive`) before attaching `DOMContentLoaded` listeners, avoiding race conditions where scripts load asynchronously after the DOM has already parsed.

