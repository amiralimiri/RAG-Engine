# ADR 0016: Unified Pluggable Conversation Memory and Session Branching Architecture

## Status
Accepted (Extends ADR 0007, ADR 0010, ADR 0014, ADR 0015)

## Context
Following the stabilization of the multi-workflow execution runtime (ADR 0007, ADR 0010) and pluggable graph harness (ADR 0015), conversational state management across workflows suffered from structural fragmentation:

1. **Siloed & Incompatible Memory Mechanisms**:
   - `ReActAgentWorkflow` managed multi-turn history internally via LangGraph's in-memory `MemorySaver()` checkpointer tied to `configurable: {"thread_id": session_id}`.
   - `SelfCorrectiveRagWorkflow` (`fix_single_llm`, `fix_single_jev`) was purely stateless; its 10-node DAG operated on `AgentState`, which lacked a `messages` sequence, ignored `session_id`, and evaluated each turn in total isolation.
   - External pluggable workflow bundles (`PluggableWorkflow`) had no standardized, cross-turn memory contract.
2. **Coupling to Graph Checkpointers**:
   - LangGraph checkpointers (`BaseCheckpointSaver`, `MemorySaver`, `AsyncPostgresSaver`) dump the entire internal execution state of a graph (nodes, execution forks, transient variables) to storage. For conversational support, checkpointing full DAG state risked bleeding transient Turn 1 retrieval artifacts (`kb_docs`, `web_results`, `retry_count`, `citations`) into Turn 2.
   - Internal tool-calling scratchpads (`AIMessage(tool_calls=...)`, `ToolMessage(...)`) polluted conversation history, exhausting token context windows and preventing users from switching workflows (e.g. from ReAct to a self-corrective DAG) across turns.
3. **No Session Resumption Across Server Restarts**:
   - `MemorySaver` was volatile in-memory storage. When the server restarted or deployed a new container, all conversation history vanished, forcing users to start over.
4. **No UI State Hydration or Session History Seam**:
   - Frontends had no REST endpoint to fetch past conversation turns (`GET /api/chat/{session_id}`) or enumerate active sessions (`GET /api/chat/sessions`) to re-render chat feeds, citation accordions, or evidence sources upon page reload.
5. **Absence of Turn Editing, Rewinding, or Forking**:
   - Users could not backtrack to correct a query at Turn $k$ without corrupting subsequent turns or losing context. Branching off an existing conversation into an alternative line of inquiry was completely unsupported.
6. **Pronoun Ambiguity in Conversational Retrieval**:
   - Follow-up queries containing pronouns (e.g., Turn 1: *"What is the VPN client?"* $\to$ Turn 2: *"How do I install it on Mac?"*) failed internal knowledge retrieval because downstream vector search was executed against the raw, decontextualized second question.

---

## Decisions

### 1. Dedicated `ConversationMemoryAdapter` Protocol Seam (`app/rag/memory/base.py`)
- Define a domain protocol `ConversationMemoryAdapter` decoupled from LangGraph checkpointer internals:
  - `aget_messages(session_id: str, limit: int | None = None) -> list[BaseMessage]`: Retrieves bounded LangChain messages (`HumanMessage`, `AIMessage`) for LLM prompting.
  - `aget_turns(session_id: str, limit: int | None = None) -> list[ConversationTurn]`: Retrieves rich domain turn models for UI hydration and API consumers.
  - `asave_turn(turn: ConversationTurn) -> None`: Persists a completed conversational turn.
  - `aclear(session_id: str) -> None`: Deletes all turns and session metadata for a session.
  - `atruncate_from(session_id: str, turn_index: int) -> None`: Safely rewinds a conversation by deleting all turns where `turn_index >= k`.
  - `afork_session(source_session_id: str, upto_turn_index: int, new_session_id: str | None = None) -> str`: Clones turns $[0 \dots \text{upto\_turn\_index}]$ into a new child session, tracking parent session lineage. Supports `upto_turn_index = -1` for Turn 0 branching (empty history with lineage).
  - `alist_sessions(limit: int = 50) -> list[SessionInfo]`: Enumerates recent sessions with title, timestamps, and lineage.
  - `aget_session(session_id: str) -> SessionInfo | None`: Retrieves session metadata.
- Define strongly-typed DTOs `ConversationTurn` and `SessionInfo` in `app/rag/memory/base.py`:
  - `ConversationTurn`: Encapsulates `session_id`, `turn_index` (0-based integer), `user_query`, `assistant_answer`, `attachments` (`list[dict]`), `citations` (`list[dict]`), `source_used`, `trace_id`, and `created_at`.
  - `SessionInfo`: Encapsulates `session_id`, `title`, `parent_session_id`, `forked_from_turn`, `created_at`, `updated_at`, and `turn_count`.

### 2. Concrete Memory Adapters (`sqlite.py`, `in_memory.py`, `noop.py`)
- **`SQLiteConversationStore` (`app/rag/memory/sqlite.py`)**:
  - The default production/development persistent store, surviving server restarts with zero external infrastructure dependencies.
  - Implemented using Python standard library `sqlite3` configured with Write-Ahead Logging (`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON;`).
  - Dispatched via Starlette's `run_in_threadpool` and synchronized with an internal `asyncio.Lock` to guarantee non-blocking event-loop execution and serialized SQLite writes.
- **`InMemoryConversationStore` (`app/rag/memory/in_memory.py`)**:
  - Thread-safe, dictionary-backed in-memory store for fast, deterministic unit testing (`pytest`) and ephemeral execution environments.
- **`NoOpMemoryStore` (`app/rag/memory/noop.py`)**:
  - Stateless pass-through store for environments where conversation memory is explicitly disabled (`settings.conversation_memory_backend = "noop"`).
- Provide a central injectable factory `get_conversation_memory()` in `app/rag/memory/__init__.py`, dynamically configured via `app/core/config.py`:
  - `settings.conversation_memory_backend: Literal["sqlite", "in_memory", "noop"] = "sqlite"`
  - `settings.conversation_db_path: str = "data/conversations.db"`
  - `settings.memory_max_turns: int = 10`

### 3. Relational Storage Schema with Lineage & Cascade Invariants
- Establish a two-table relational SQLite schema with strict foreign keys:
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

### 4. Centralized Memory Lifecycle in `WorkflowRunner`
- Inject `memory: ConversationMemoryAdapter | None = None` into `WorkflowRunner.__init__`, defaulting to `get_conversation_memory()`.
- **Hydration (`_prepare_execution`)**:
  1. If `session_id` is present and memory is enabled:
     - Check if `rewind_turn_index` is requested in `WorkflowInput`. If so, hydrate only turns $[0 \dots \text{rewind\_turn\_index} - 1]$ (safe pre-flight hydration).
     - Otherwise, retrieve bounded messages via `memory.aget_messages(session_id, limit=max_turns)`.
     - Inject hydrated messages into `WorkflowInput.messages` before passing to `wf_def.to_initial_state()`.
  2. If `session_id` is omitted or memory is empty:
     - Fall back to client-supplied `WorkflowInput.messages` or initialize an empty sequence.
- **Persistence (`_finalize_execution`)**:
  1. Memory persistence is atomic and post-execution: occurs only after full response generation succeeds in both batch (`execute`) and streaming (`stream`) pipelines.
  2. If execution fails with an unhandled exception or client disconnects mid-stream, persistence is strictly skipped (corrupted or partial turns are never stored).
  3. If `rewind_turn_index` was specified and execution succeeded:
     - Atomically invoke `memory.atruncate_from(session_id, rewind_turn_index)` before persisting the new turn at `turn_index = rewind_turn_index`.
  4. Persist strictly canonical `(HumanMessage, AIMessage)` semantic turns:
     - Intermediate tool call scratchpads (`ToolMessage`, tool call deltas) are excluded from memory storage, ensuring zero context leakage and universal interoperability across workflow switches.
     - Multimodal attachments are serialized into `attachments_json` and restored via `HumanMessage.additional_kwargs["attachments"]`.

### 5. Unified State Contract & Transient Variable Scrubbing
- Extend `AgentState` (`app/rag/state.py`) and `StandardWorkflowState` (`app/rag/workflows/base.py`) with:
  ```python
  messages: Annotated[list[BaseMessage], operator.add]
  ```
- Mandate per-turn transient state scrubbing in `to_initial_state`:
  - When initializing graph state for a new turn, all transient retrieval and grading artifacts (`kb_docs`, `web_results`, `kb_grade`, `web_grade`, `retry_count`, `citations`, `retrieved_contexts`, `trace`) are explicitly reset to empty defaults.
  - Initial `current_query` defaults to `question`.

### 6. Decoupling `ReActAgentWorkflow` from Internal Checkpointers
- Update `ReActAgentWorkflow` (`app/rag/workflows/react_agent.py`) so `checkpointer=None` by default.
- Since `WorkflowRunner` centrally manages message hydration into `WorkflowInput.messages` and persistence via `ConversationMemoryAdapter`, `ReActAgentWorkflow` compiles as a pure runnable without dual-persistence or checkpointer collision.

### 7. Conversational Query Condensation in Self-Corrective DAG
- Update `create_agentic_rag_graph` in `app/rag/engine.py` to support conversational retrieval:
  - Add conditional edge from `START`:
    - If `len(messages) <= 1` (Turn 1 or stateless), route directly to `route_question` (0ms overhead).
    - If `len(messages) > 1`, route to a new `condense_query` node.
  - The `condense_query` node uses a fast LLM call to resolve pronouns and references across prior messages, updating `state["current_query"]` with a standalone search query while preserving `state["question"]` verbatim.
  - Downstream retrieval (`retrieve_kb`, `search_web`, `rewrite_query`) operates on `current_query`.
  - Generation nodes (`generate_from_kb`, `generate_from_web`, `direct_answer`) receive prior messages (`messages[:-1]`) as structured `history` in prompt invocations, preserving 100% backward compatibility for single-turn templates.

### 8. Turn Rewind and Conversation Forking
- **In-Place Rewind (Truncation)**:
  - Atomic execution via `WorkflowInput.rewind_turn_index = k`: trims history before execution, runs turn $k$, and commits truncation only on success.
  - Standalone endpoint `POST /api/chat/{session_id}/rewind` with `RewindRequest(turn_index=k)`.
- **Conversation Forking (Branching)**:
  - Dedicated endpoint `POST /api/chat/{session_id}/fork` with `ForkRequest(upto_turn_index=k, new_session_id=None)`.
  - Clones turns $[0 \dots k]$ into `new_session_id`, sets `parent_session_id = session_id` and `forked_from_turn = k`, and returns the new session metadata. Supports $k = -1$ for Turn 0 branching.

### 9. REST API Transport Contracts (`app/api/routes.py` & `app/api/schemas.py`)
- Add endpoints to `app/api/routes.py`:
  - `GET /api/chat/sessions`: Lists recent chat sessions with lineage and timestamps.
  - `GET /api/chat/{session_id}`: Retrieves full session metadata and rich `ConversationTurn` history.
  - `DELETE /api/chat/{session_id}`: Purges session and cascades turn deletion.
  - `POST /api/chat/{session_id}/rewind`: Standalone truncation of turns $\ge k$.
  - `POST /api/chat/{session_id}/fork`: Branches session up to turn $k$ into a new session.
- Expand `ChatRequest` additively with optional `rewind_turn_index: int | None = None`.

### 10. Frontend Hydration & Branching UX (`static/js/app.js`)
- **Session Resumption**:
  - On page load, read `rag_session_id` from `localStorage`. If found, call `GET /api/chat/{session_id}` to hydrate the chat container with prior messages, source badges, and citation accordions. If 404, generate a fresh UUID.
- **Session Management**:
  - Provide a session selector/sidebar populated via `GET /api/chat/sessions`.
  - "New Chat" mints a new UUID in `localStorage` without deleting the old session.
  - "Clear Conversation" invokes `DELETE /api/chat/{session_id}` and clears the view.
- **Edit & Rewind / Branching Controls**:
  - User message bubbles feature an Edit button.
  - Clicking Edit allows the user to:
    1. **Submit in-place (Rewind)**: Calls `chat_stream` with `rewind_turn_index: k`, truncating following turns and streaming the updated answer.
    2. **Branch to new chat (Fork)**: Calls `POST /api/chat/{session_id}/fork`, switches active `rag_session_id` to the fork, and executes the updated turn on the new branch.

### 11. Structured Logging & Telemetry Context
- Instrument `_prepare_execution` with structured log `conversation_memory_hydrated` (`session_id`, `turns_loaded`, `rewind_turn_index`).
- Bind `turn_count` to structlog contextvars.
- Instrument `_finalize_execution` with `conversation_turn_persisted` (`session_id`, `turn_index`, `source_used`).
- Inject `turn_count` and session lineage tags into Langfuse v5 trace metadata.

---

## Consequences

### Positive
- **Pluggable & Durable**: Memory is a first-class, swappable abstraction. SQLite persistence guarantees chat histories survive server restarts without external services.
- **Uniform Workflow Interoperability**: ReAct agents, self-corrective DAGs, and external bundles share identical conversational memory. Users can switch workflows mid-conversation seamlessly.
- **Clean Context Windows**: Storing only semantic user-assistant pairs eliminates tool scratchpad bloat and prevents context-length errors.
- **Conversational Retrieval Accuracy**: Query condensation resolves ambiguous follow-up pronouns into grounded enterprise search queries.
- **Advanced UX (Rewind & Forking)**: Users can correct prior turns in place or branch conversations safely without data loss.
- **Zero Regressions**: Stateless calls (`session_id=None` or `NoOpMemoryStore`) continue to execute with 0ms memory overhead.

### Negative
- **Database I/O**: Local SQLite transactions add small disk I/O (~1–5ms) during turn finalization, negligible compared to LLM generation latency.
- **Condensation Overhead**: Multi-turn DAG queries invoke a fast query condensation LLM call (~200–400ms) on follow-up turns to guarantee retrieval grounding.
