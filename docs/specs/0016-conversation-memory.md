# Technical Engineering Specification: Unified Pluggable Conversation Memory & Session Branching (ADR-0016)

## 1. Executive Summary & Objective

This specification details the implementation of a unified, pluggable multi-turn conversation memory architecture across the enterprise IT support platform, fulfilling the requirements established in **ADR-0016** and **GLOSSARY.md**.

### Primary Objectives:
1. **Pluggable Memory Seam**: Establish `ConversationMemoryAdapter` as a first-class domain protocol in `app/rag/memory/`, providing three interchangeable implementations (`SQLiteConversationStore`, `InMemoryConversationStore`, `NoOpMemoryStore`).
2. **Durable Persistence & Session Resumption**: Persist chat sessions across server restarts using standard library `sqlite3` in Write-Ahead Logging (WAL) mode, enabling returning users to view past messages, citations, and evidence sources, and resume conversations seamlessly.
3. **Uniformity Across All Workflows**: Provide uniform multi-turn conversational behavior across all workflow paradigms (Self-Corrective RAG DAGs, ReAct agents, and external pluggable bundles) via `WorkflowRunner`, decoupling `ReActAgentWorkflow` from its hardcoded `MemorySaver()` checkpointer.
4. **Transient State Scrubbing & Clean Context**: Persist strictly canonical `(HumanMessage, AIMessage)` semantic turns (omitting intermediate tool scratchpads) and scrub per-turn transient variables in `AgentState` and `StandardWorkflowState` to prevent turn-to-turn context leakage.
5. **Conversational Query Condensation**: Add a conditional `condense_query` node to the 10-node DAG in `app/rag/engine.py` that contextualizes multi-turn follow-up queries containing pronouns before knowledge retrieval.
6. **Advanced Turn Operations**: Implement in-place turn rewind/truncation (with safe commit only on successful execution) and conversation branching/forking with parent session lineage tracking.
7. **REST APIs & Frontend UI**: Expose RESTful endpoints for session management, turn history, rewind, and forking, and upgrade `static/js/app.js` with server-first session resumption, turn editing, and branching controls.

---

## 2. Architecture & Domain Contracts (`app/rag/memory/`)

```
app/rag/memory/
├── __init__.py           # Factory entrypoint: get_conversation_memory()
├── base.py               # Domain DTOs (ConversationTurn, SessionInfo) & ConversationMemoryAdapter protocol
├── sqlite.py             # SQLiteConversationStore (standard library sqlite3, WAL mode, threadpool dispatch)
├── in_memory.py          # InMemoryConversationStore (dict-based, thread-safe, for fast pytest suites)
└── noop.py               # NoOpMemoryStore (stateless pass-through)
```

### 2.1 Domain Data Transfer Objects (`app/rag/memory/base.py`)

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Normalized domain model representing a completed conversational exchange."""
    
    session_id: str = Field(..., description="Unique conversational session identifier")
    turn_index: int = Field(..., ge=0, description="0-based monotonic index within the session")
    user_query: str = Field(..., description="Verbatim text submitted by the user")
    assistant_answer: str = Field(..., description="Final assistant response text")
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Serialized list of multimodal file/image attachments submitted by user",
    )
    citations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Structured citations and reference sources cited in assistant answer",
    )
    source_used: str = Field(
        default="direct",
        description="Primary evidence source utilized (e.g. vectorstore, web_search, react_agent, direct)",
    )
    trace_id: str | None = Field(
        default=None,
        description="Langfuse telemetry trace identifier associated with this turn",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the turn was completed and recorded",
    )

    def to_messages(self) -> list[BaseMessage]:
        """Convert domain turn into standard LangChain (HumanMessage, AIMessage) pair."""
        kwargs = {}
        if self.attachments:
            kwargs["attachments"] = self.attachments
        user_msg = HumanMessage(content=self.user_query, additional_kwargs=kwargs)
        ai_msg = AIMessage(content=self.assistant_answer)
        return [user_msg, ai_msg]


class SessionInfo(BaseModel):
    """Metadata summary of a conversational session."""
    
    session_id: str = Field(..., description="Unique session identifier")
    title: str = Field(..., description="Human-readable title (derived from first user query)")
    parent_session_id: str | None = Field(
        default=None,
        description="Originating session identifier if branched/forked from another session",
    )
    forked_from_turn: int | None = Field(
        default=None,
        description="Turn index in parent session at which the branch occurred",
    )
    created_at: datetime = Field(..., description="UTC creation timestamp")
    updated_at: datetime = Field(..., description="UTC last-activity timestamp")
    turn_count: int = Field(default=0, ge=0, description="Total number of completed turns in this session")
```

### 2.2 `ConversationMemoryAdapter` Protocol Seam (`app/rag/memory/base.py`)

```python
@runtime_checkable
class ConversationMemoryAdapter(Protocol):
    """Structural protocol defining lifecycle contracts for conversational memory persistence."""

    async def aget_messages(self, session_id: str, limit: int | None = None) -> list[BaseMessage]:
        """Retrieve bounded chronological sequence of LangChain BaseMessages for LLM prompting."""
        ...

    async def aget_turns(self, session_id: str, limit: int | None = None) -> list[ConversationTurn]:
        """Retrieve rich domain turn objects for UI hydration and API consumers."""
        ...

    async def asave_turn(self, turn: ConversationTurn) -> None:
        """Persist a completed turn and update session metadata."""
        ...

    async def aclear(self, session_id: str) -> None:
        """Purge all turns and delete the specified session."""
        ...

    async def atruncate_from(self, session_id: str, turn_index: int) -> None:
        """Safely delete all turns with turn_index >= turn_index for in-place rewind."""
        ...

    async def afork_session(
        self,
        source_session_id: str,
        upto_turn_index: int,
        new_session_id: str | None = None,
    ) -> str:
        """Clone turns [0 .. upto_turn_index] into a new child session, tracking parent lineage.
        
        If upto_turn_index == -1 (Turn 0 branching), creates a new empty session with
        parent_session_id set to source_session_id and forked_from_turn set to 0.
        """
        ...

    async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]:
        """Enumerate recent sessions ordered by updated_at descending."""
        ...

    async def aget_session(self, session_id: str) -> SessionInfo | None:
        """Look up metadata for a specific session."""
        ...
```

### 2.3 `SQLiteConversationStore` Implementation Details (`app/rag/memory/sqlite.py`)

- **Concurrency & Threading**:
  - Uses standard library `sqlite3` wrapped via `starlette.concurrency.run_in_threadpool`.
  - Guarded by an `asyncio.Lock()` instance for write transactions to serialize SQLite writes without locking the FastAPI event loop.
- **Connection Configuration**:
  ```python
  conn = sqlite3.connect(self.db_path, check_same_thread=False)
  conn.row_factory = sqlite3.Row
  conn.execute("PRAGMA journal_mode=WAL;")
  conn.execute("PRAGMA busy_timeout=5000;")
  conn.execute("PRAGMA foreign_keys=ON;")
  ```
- **Relational DDL**:
  ```sql
  CREATE TABLE IF NOT EXISTS conversation_sessions (
      session_id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      parent_session_id TEXT NULL REFERENCES conversation_sessions(session_id) ON DELETE SET NULL,
      forked_from_turn INTEGER NULL,
      created_at TIMESTAMP NOT NULL,
      updated_at TIMESTAMP NOT NULL
  );

  CREATE TABLE IF NOT EXISTS conversation_turns (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id) ON DELETE CASCADE,
      turn_index INTEGER NOT NULL,
      user_query TEXT NOT NULL,
      assistant_answer TEXT NOT NULL,
      attachments_json TEXT NULL,
      citations_json TEXT NULL,
      source_used TEXT NOT NULL,
      trace_id TEXT NULL,
      created_at TIMESTAMP NOT NULL,
      UNIQUE(session_id, turn_index)
  );

  CREATE INDEX IF NOT EXISTS idx_turns_session_idx ON conversation_turns(session_id, turn_index);
  CREATE INDEX IF NOT EXISTS idx_sessions_updated ON conversation_sessions(updated_at DESC);
  ```
- **Session Upsert on Turn Persistence**:
  When `asave_turn(turn)` is called:
  - If `conversation_sessions` row does not exist for `turn.session_id`:
    - Insert row with `session_id=turn.session_id`, `title=turn.user_query[:60].strip()`, `created_at=turn.created_at`, `updated_at=turn.created_at`.
  - Else:
    - Update `updated_at=turn.created_at`.
  - Insert or replace into `conversation_turns`.

### 2.4 `InMemoryConversationStore` Implementation Details (`app/rag/memory/in_memory.py`)

- Thread-safe via `threading.RLock()`.
- Stores data in `dict[str, SessionInfo]` and `dict[str, list[ConversationTurn]]`.
- Replicates all behaviors of `SQLiteConversationStore` (cascading deletes, bounded queries, turn truncation, branching lineage).
- Serves as the primary double for unit tests in `pytest`.

### 2.5 `NoOpMemoryStore` Implementation Details (`app/rag/memory/noop.py`)

- Stateless pass-through implementation:
  - `aget_messages` returns `[]`.
  - `aget_turns` returns `[]`.
  - `asave_turn`, `aclear`, `atruncate_from` perform no operation.
  - `afork_session` returns `new_session_id or uuid.uuid4().hex`.
  - `alist_sessions` returns `[]`.
  - `aget_session` returns `None`.

---

## 3. Configuration & Dependency Injection (`app/core/config.py`)

### 3.1 Settings Configuration

Add the following fields to `Settings` in `app/core/config.py`:

```python
    # Conversational Memory Settings (ADR-0016)
    conversation_memory_backend: Literal["sqlite", "in_memory", "noop"] = Field(
        default="sqlite",
        description="Backing storage adapter for multi-turn conversation memory",
    )
    conversation_db_path: str = Field(
        default="data/conversations.db",
        description="Filesystem path for local SQLite conversation database",
    )
    memory_max_turns: int = Field(
        default=10,
        ge=1,
        description="Maximum number of historical turns to hydrate into workflow prompt context",
    )
```

### 3.2 Factory Singleton (`app/rag/memory/__init__.py`)

```python
from functools import lru_cache
from app.core.config import get_settings
from app.rag.memory.base import ConversationMemoryAdapter, ConversationTurn, SessionInfo
from app.rag.memory.in_memory import InMemoryConversationStore
from app.rag.memory.noop import NoOpMemoryStore
from app.rag.memory.sqlite import SQLiteConversationStore

_MEMORY_SINGLETON: ConversationMemoryAdapter | None = None


def get_conversation_memory() -> ConversationMemoryAdapter:
    """Resolve and return configured ConversationMemoryAdapter singleton."""
    global _MEMORY_SINGLETON
    if _MEMORY_SINGLETON is not None:
        return _MEMORY_SINGLETON

    settings = get_settings()
    backend = settings.conversation_memory_backend
    if backend == "sqlite":
        _MEMORY_SINGLETON = SQLiteConversationStore(db_path=settings.conversation_db_path)
    elif backend == "in_memory":
        _MEMORY_SINGLETON = InMemoryConversationStore()
    elif backend == "noop":
        _MEMORY_SINGLETON = NoOpMemoryStore()
    else:
        _MEMORY_SINGLETON = InMemoryConversationStore()

    return _MEMORY_SINGLETON


def reset_conversation_memory() -> None:
    """Reset module-level singleton (useful for test isolation)."""
    global _MEMORY_SINGLETON
    _MEMORY_SINGLETON = None
```

---

## 4. Runtime Orchestration Seam (`app/rag/workflows/runner.py`)

### 4.1 Dependency Injection in `WorkflowRunner`

Update `WorkflowRunner.__init__`:
```python
    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        *,
        memory: ConversationMemoryAdapter | None = None,
        evaluator: Evaluator | None = None,
        callback_factory: Callable[..., Any] | None = None,
        trace_extractor: Callable[[Any], str | None] | None = None,
        telemetry_flusher: Callable[[], None] | None = None,
        eval_task_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else get_workflow_registry()
        self._memory = memory
        self._evaluator = evaluator
        # ... remaining initializers
```

Expose property:
```python
@property
def memory(self) -> ConversationMemoryAdapter:
    if self._memory is not None:
        return self._memory
    from app.rag.memory import get_conversation_memory

    return get_conversation_memory()
```

### 4.2 Pre-Flight Memory Hydration (`_prepare_execution`)

Extend `WorkflowRunner._prepare_execution` to hydrate conversational history:
1. Extract `session_id = getattr(request, "session_id", None)` and `rewind_turn_index = getattr(request, "rewind_turn_index", None)`.
2. If `session_id` is present:
   - If `rewind_turn_index is not None` (atomic edit-and-run):
     - Fetch turns: `all_turns = await self.memory.aget_turns(session_id)`.
     - Slice turns `[0 .. rewind_turn_index - 1]`.
     - Convert sliced turns into messages: `hydrated_messages = [msg for t in sliced_turns for msg in t.to_messages()]`.
   - Else:
     - `hydrated_messages = await self.memory.aget_messages(session_id, limit=settings.memory_max_turns)`.
   - If `hydrated_messages` is non-empty:
     - Set `request.messages = hydrated_messages`.
   - Bind `turn_count = len(hydrated_messages) // 2` to `structlog.contextvars`.
   - Log `conversation_memory_hydrated` event.
3. If `session_id` is absent or `hydrated_messages` is empty:
   - Retain client-supplied `request.messages` (if any).

### 4.3 Atomic Turn Finalization & Safe Truncation (`_finalize_execution`)

Extend `_finalize_execution` (or an `async` finalizer helper):
1. Check if execution succeeded and produced a valid non-empty answer.
2. If `request.session_id` is present:
   - Determine current `turn_index`:
     - If `request.rewind_turn_index is not None`:
       - First atomically truncate in storage: `await self.memory.atruncate_from(request.session_id, request.rewind_turn_index)`.
       - Target `turn_index = request.rewind_turn_index`.
     - Else:
       - Existing turns: `existing = await self.memory.aget_turns(request.session_id)`.
       - `turn_index = len(existing)`.
   - Construct `turn = ConversationTurn(...)`:
     - `session_id=request.session_id`
     - `turn_index=turn_index`
     - `user_query=request.question`
     - `assistant_answer=domain_output.answer`
     - `attachments=request.attachments or []`
     - `citations=domain_output.citations`
     - `source_used=domain_output.source_used`
     - `trace_id=resolved_tid`
   - Call `await self.memory.asave_turn(turn)`.
   - Log `conversation_turn_persisted` event with `turn_index`.
3. In `stream()`:
   - Call the async persistence routine in the `finally` block of `_stream_generator` **only** if the answer completed successfully without an unhandled exception or client disconnect.

---

## 5. Workflow Topologies & Uniform Conversational Contracts

### 5.1 Canonical State Contracts

1. **`AgentState` (`app/rag/state.py`)**:
   ```python
   class AgentState(TypedDict):
       question: str
       current_query: str
       messages: Annotated[list[BaseMessage], operator.add]
       kb_docs: list[Document]
       web_results: str
       kb_grade: str
       web_grade: str
       answer: str
       source_used: str
       retry_count: int
       trace: Annotated[list[str], operator.add]
       citations: list[dict]
       trace_id: NotRequired[str]
       retrieved_contexts: NotRequired[list[str]]
       attachments: NotRequired[list[dict[str, Any]]]
   ```

2. **`StandardWorkflowState` (`app/rag/workflows/base.py`)**:
   Ensure `messages: Sequence[BaseMessage]` is present and documented.

### 5.2 Per-Turn State Scrubbing (`SelfCorrectiveRagWorkflow.to_initial_state`)

In `app/rag/workflows/self_corrective.py`:
```python
    def to_initial_state(self, input: WorkflowInput | Any, trace_id: str | None = None) -> dict[str, Any]:
        attachments = parse_raw_attachments(getattr(input, "attachments", None))
        question = getattr(input, "question", "")
        raw_messages = getattr(input, "messages", None)
        messages = parse_raw_messages(raw_messages, question)

        return {
            "question": question,
            "current_query": question,  # Default to question for Turn 1
            "messages": messages,
            "kb_docs": [],
            "web_results": "",
            "kb_grade": "",
            "web_grade": "",
            "answer": "",
            "source_used": "",
            "retry_count": 0,
            "trace": [],
            "citations": [],
            "trace_id": trace_id or getattr(input, "trace_id", None),
            "retrieved_contexts": [],
            "attachments": attachments,
        }
```

### 5.3 Conditional Query Condensation in Self-Corrective DAG (`app/rag/engine.py`)

1. **Routing from `START`**:
   ```python
   def route_start(state: AgentState) -> Literal["condense_query", "route_question"]:
       # If history has prior messages before the current question turn
       messages = state.get("messages", [])
       if len(messages) > 1:
           return "condense_query"
       return "route_question"
   ```

2. **`condense_query` Node**:
   ```python
   async def node_condense_query(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
       structlog.contextvars.bind_contextvars(step="condense_query")
       history = state.get("messages", [])[:-1]
       current_question = state["question"]

       prompt_obj = get_workflow_prompt("rag:condense_query")
       # Fallback template if prompt CMS is empty:
       # "Given the following conversation and a follow up question, rephrase the follow up question to be a standalone search query.\nChat History: {chat_history}\nFollow Up: {question}\nStandalone question:"
       run_cfg = _build_prompt_config(prompt_obj, config)
       standalone_query = await active_model.ainvoke(...)
       cleaned_query = (
           standalone_query.content.strip() if hasattr(standalone_query, "content") else str(standalone_query).strip()
       )

       return {
           "current_query": cleaned_query,
           "trace": [f"Query contextualized → '{cleaned_query}'"],
       }
   ```

3. **Graph Compilation**:
   ```python
   graph.add_node("condense_query", node_condense_query)
   graph.add_conditional_edges(
       START,
       route_start,
       {
           "condense_query": "condense_query",
           "route_question": "route_question",
       },
   )
   graph.add_edge("condense_query", "route_question")
   ```

4. **Generation Nodes Multi-Turn Context**:
   Update `generate_from_kb`, `generate_from_web`, and `direct_answer` in `app/rag/engine.py` to pass `history=state.get("messages", [])[:-1]` when invoking prompt templates or chat models, gracefully falling back if prompt templates only expect single-turn parameters.

### 5.4 Decoupling `ReActAgentWorkflow` Checkpointer (`app/rag/workflows/react_agent.py`)

Update `ReActAgentWorkflow`:
- Change `checkpointer: Any = None` default to `None`.
- In `get_compiled_graph`:
  ```python
  self._compiled_graph = create_agent(
      model=active_llm,
      tools=active_tools,
      checkpointer=self.checkpointer,  # None by default!
      system_prompt=self.system_prompt,
  )
  ```
- All conversational history is injected via `WorkflowInput.messages` by `WorkflowRunner`, ensuring zero checkpointer collision or dual-save bugs.

---

## 6. REST API Transport Contracts & Schemas

### 6.1 Pydantic Request/Response Models (`app/api/schemas.py`)

```python
class ChatTurnResponse(BaseModel):
    """Schema for a completed turn in API responses."""

    turn_index: int
    user_query: str
    assistant_answer: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    source_used: str = "direct"
    trace_id: str | None = None
    created_at: datetime


class SessionDetailResponse(BaseModel):
    """Schema for session metadata and full turn history."""

    session_id: str
    title: str
    parent_session_id: str | None = None
    forked_from_turn: int | None = None
    created_at: datetime
    updated_at: datetime
    turn_count: int
    turns: list[ChatTurnResponse] = Field(default_factory=list)


class SessionSummaryResponse(BaseModel):
    """Schema for session list entries."""

    session_id: str
    title: str
    parent_session_id: str | None = None
    forked_from_turn: int | None = None
    created_at: datetime
    updated_at: datetime
    turn_count: int


class SessionListResponse(BaseModel):
    """Schema for session enumeration endpoint."""

    sessions: list[SessionSummaryResponse]
    total: int


class RewindRequest(BaseModel):
    """Request payload for standalone turn truncation."""

    turn_index: int = Field(..., ge=0, description="0-based turn index from which to truncate (inclusive)")


class ForkRequest(BaseModel):
    """Request payload for branching a session."""

    upto_turn_index: int = Field(
        ...,
        ge=-1,
        description="Turn index up to which history is cloned inclusive (-1 for empty session branching)",
    )
    new_session_id: str | None = Field(
        default=None,
        description="Optional pre-generated UUID v4 for the child session",
    )
```

Add to `ChatRequest`:
```python
    rewind_turn_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional turn index from which to rewind and re-execute atomically",
    )
```
Pass `rewind_turn_index` through `to_workflow_input()`.

### 6.2 REST Routes (`app/api/routes.py`)

1. **`GET /api/chat/sessions`**:
   - Queries `memory.alist_sessions(limit=50)`.
   - Returns `SessionListResponse`.
   - *Note*: Registered before `/api/chat/{session_id}` to prevent route collision.

2. **`GET /api/chat/{session_id}`**:
   - Queries `memory.aget_session(session_id)` and `memory.aget_turns(session_id)`.
   - Returns 404 if session not found.
   - Returns `SessionDetailResponse` with rich turns.

3. **`DELETE /api/chat/{session_id}`**:
   - Invokes `await memory.aclear(session_id)`.
   - Returns `{"status": "deleted", "session_id": session_id}`.

4. **`POST /api/chat/{session_id}/rewind`**:
   - Accepts `payload: RewindRequest`.
   - Invokes `await memory.atruncate_from(session_id, payload.turn_index)`.
   - Returns `{"status": "rewound", "session_id": session_id, "turn_index": payload.turn_index}`.

5. **`POST /api/chat/{session_id}/fork`**:
   - Accepts `payload: ForkRequest`.
   - Generates or validates `new_session_id`.
   - Invokes `await memory.afork_session(session_id, payload.upto_turn_index, new_session_id)`.
   - Returns `SessionDetailResponse` for the new session.

---

## 7. Frontend Client Architecture & UX (`static/js/app.js`)

### 7.1 Server-First Session Resumption Flow

1. **Initialization (`initSessions`)**:
   - Inspect `storage.getItem("rag_current_session_id")`.
   - If present:
     - Fetch `GET /api/chat/{currentSessionId}`.
     - On 200 OK:
       - Render transcript from server turns using existing `addMessage(...)` helper (with citations, source badges, and trace IDs).
       - Mark `sessionData` in local memory.
     - On 404:
       - Mint new UUID v4, set in storage, render empty chat.
   - Also trigger `loadRecentSessionsFromServer()` via `GET /api/chat/sessions` to populate the sidebar.

2. **Session Switching (`switchSession`)**:
   - Calls `GET /api/chat/{sessionId}`.
   - Populates chat messages and citations.
   - Updates `currentSessionId`.

3. **"New Chat" vs. "Clear Conversation"**:
   - **"New Chat"**: Mints a new UUID v4, updates active ID, clears DOM feed, keeps previous session intact in database and sidebar.
   - **"Clear Conversation"**: Invokes `DELETE /api/chat/{sessionId}`, removes from sidebar, and initiates "New Chat".

### 7.2 Turn Edit & Rewind / Forking UI Controls

1. **Edit Action on User Message Bubbles**:
   - In `addMessage(role, text, ...)`:
     - If `role === "user"`:
       - Attach `dataset.turnIndex = currentTurnIndex`.
       - Render a subtle edit action button (icon/button: ✏️).
2. **Edit Mode Activation**:
   - Clicking Edit on Turn $k$:
     - Populates the query textarea with the turn's user text.
     - Displays an active **Action Banner** above the input box:
       ```html
       <div id="turnActionBanner" class="turn-action-banner">
         <span>Editing Turn #k</span>
         <button id="btnRewindSubmit" class="btn-sm btn-primary">Edit &amp; Rewind Here</button>
         <button id="btnForkSubmit" class="btn-sm btn-secondary">Branch to New Chat</button>
         <button id="btnCancelEdit" class="btn-sm btn-ghost">Cancel</button>
       </div>
       ```
3. **Execution Handling**:
   - **Edit & Rewind Here**:
     - Sets `pendingRewindTurnIndex = k`.
     - Submits `POST /api/chat/stream` with `rewind_turn_index: k`.
     - Prunes DOM bubbles $\ge k$ and streams the replacement turn.
   - **Branch to New Chat**:
     - Calls `POST /api/chat/{currentSessionId}/fork` with `upto_turn_index: k - 1`.
     - Receives `new_session_id`.
     - Switches active session to `new_session_id`, sets input, and streams the new turn.

---

## 8. Error-Handling, Concurrency & Edge-Case Guardrails

1. **Client Disconnects Mid-Stream**:
   - In `WorkflowRunner._stream_generator`:
     - If the client disconnects before the final generation event or if an `asyncio.CancelledError` is raised, memory persistence is skipped.
     - The database retains only completed, verified turns.
2. **Unhandled Inference Failures**:
   - If an unhandled exception occurs in `execute()` or `stream()`, no turn is saved.
   - If the error is handled gracefully via `handle_error(exc, state)` and a fallback response is yielded, that fallback response is persisted as a valid turn so the user can continue or ask for clarification.
3. **Atomic Rewind Failure Safety**:
   - For atomic edit-and-run requests (`rewind_turn_index = k`), history is read up to $k-1$ in `_prepare_execution`, but truncation of turns $\ge k$ is **deferred** until after the new turn succeeds in `_finalize_execution`. If the LLM throws or fails, original turns $\ge k$ remain intact!
4. **Turn 0 Branching (`upto_turn_index = -1`)**:
   - Handles the edge case where the user edits the very first question and chooses "Branch to New Chat".
   - A new session is created with `parent_session_id = source_session_id` and `forked_from_turn = 0`, but 0 turns are cloned.
5. **SQLite Locking & Busy Timeouts**:
   - Enabled `PRAGMA busy_timeout=5000;` and `PRAGMA journal_mode=WAL;`.
   - Write operations use an in-process `asyncio.Lock` to serialize writes across concurrent coroutines.

---

## 9. Comprehensive TDD Test Suite Plan

### 9.1 Unit Tests: `tests/rag/test_conversation_memory.py`
- Test `SQLiteConversationStore` with temporary SQLite database (`tmp_path / "test.db"`):
  - `test_save_and_get_turns`: Verify turn persistence, ordering, and data integrity.
  - `test_get_messages_conversion`: Verify `to_messages()` outputs proper `HumanMessage` and `AIMessage` pairs.
  - `test_bounded_retrieval`: Verify `limit` parameter limits returned turns/messages to latest $N$.
  - `test_truncate_from`: Verify rewind correctly deletes turns $\ge k$ and preserves turns $< k$.
  - `test_fork_session`: Verify forking copies turns $[0 \dots k]$ and sets `parent_session_id` and `forked_from_turn`.
  - `test_fork_session_turn_zero`: Verify `upto_turn_index = -1` creates an empty child session with parent lineage.
  - `test_clear_session_cascade`: Verify `aclear` cascades and removes session and turns.
  - `test_list_sessions_ordering`: Verify sessions are ordered by `updated_at` descending.
- Test `InMemoryConversationStore` against the exact same test cases using a parameterized fixture.
- Test `NoOpMemoryStore` returns empty collections and no-ops safely.

### 9.2 Integration Tests: `tests/rag/test_workflow_runner_memory.py`
- Test `WorkflowRunner` with injected `InMemoryConversationStore`:
  - `test_runner_hydrates_history`: Turn 1 generates answer and persists. Turn 2 sees Turn 1 in `WorkflowInput.messages`.
  - `test_runner_skips_tool_scratchpads`: Ensure ReAct agent tool calls are excluded from memory, storing only user and final assistant messages.
  - `test_runner_atomic_rewind`: Submit `rewind_turn_index = 1` on a 3-turn session. Verify turns $\ge 1$ are replaced by the new turn.
  - `test_runner_aborts_persistence_on_error`: Trigger an error during node execution; verify memory remains uncorrupted.
  - `test_runner_streaming_persistence`: Execute `runner.stream()`, consume all tokens, verify turn is persisted in memory upon completion.

### 9.3 Engine & DAG Tests: `tests/test_workflow_engine_memory.py`
- Test `create_agentic_rag_graph` multi-turn behavior:
  - `test_engine_turn_1_skips_condense`: Single message routes directly from `START` to `route_question`.
  - `test_engine_turn_2_executes_condense`: Multiple messages route from `START` to `condense_query`, populating `current_query`.
  - `test_engine_transient_state_scrubbing`: Ensure `kb_docs`, `retry_count`, and `citations` are cleared between turns.
  - `test_react_agent_without_internal_checkpointer`: Ensure `ReActAgentWorkflow` compiles with `checkpointer=None` and relies on `messages` passed in input.

### 9.4 API Route Tests: `tests/api/test_routes_chat_memory.py`
- Test FastAPI routes with `TestClient`:
  - `test_get_sessions_empty_and_populated`: `GET /api/chat/sessions`.
  - `test_get_session_detail_and_404`: `GET /api/chat/{session_id}`.
  - `test_delete_session`: `DELETE /api/chat/{session_id}`.
  - `test_rewind_endpoint`: `POST /api/chat/{session_id}/rewind`.
  - `test_fork_endpoint`: `POST /api/chat/{session_id}/fork`.
  - `test_chat_with_rewind_turn_index`: `POST /api/chat` with `rewind_turn_index`.

### 9.5 Frontend Tests: `tests/test_frontend_memory.py`
- Test `static/js/app.js` using Python Node/JsDom harness:
  - `test_frontend_resumes_session_from_server`: Mock `fetch` returning session turns; verify chat bubbles and citations render.
  - `test_frontend_handles_404_resumption`: Verify fresh UUID is generated on 404.
  - `test_frontend_edit_and_rewind_action`: Verify clicking Edit & Rewind sets payload `rewind_turn_index`.
  - `test_frontend_branch_action`: Verify clicking Branch calls fork endpoint and switches active session.
