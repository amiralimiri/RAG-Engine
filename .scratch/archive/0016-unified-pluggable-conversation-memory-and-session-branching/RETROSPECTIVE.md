# Retrospective: Milestone 0016 (Unified Pluggable Conversation Memory & Session Branching)

**Date:** November 2, 2026  
**Status:** Completed, Reviewed, Remediated, & Archived  
**ADR Reference:** `docs/adr/0016-unified-pluggable-conversation-memory-and-session-branching.md`  
**Specification:** ADR-0016 Architecture & Seam Definitions  
**Work Packages / Tickets:** Tickets 01–07 (Archived under `.scratch/archive/0016-unified-pluggable-conversation-memory-and-session-branching/issues/`)  
**Domain Guidelines:** `docs/agents/conversation-memory.md` | `CODING_STANDARDS.md` | `docs/agents/workflow-execution.md`  
**Test Suite:** 653 passed, 0 failed, 100% offline-capable, clean pyright across memory, runner, and route packages  
**Commit Reference:** `1ac9ea7`  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0016 unified multi-turn conversation persistence, session resumption, atomic rewind, and session branching across all workflows in the RAG engine. Prior to this milestone, conversations were either transient or tied to ad-hoc in-memory checkpointers without server-side lineage, history hydration across workflows, or persistent session management.

With ADR-0016, we established a standalone domain protocol seam (`ConversationMemoryAdapter`), implemented interchangeable zero-shim adapters (`NoOpConversationStore`, `InMemoryConversationStore`, and `SQLiteConversationStore`), wired conversation memory into `WorkflowRunner` for both batch and streaming flows, exposed RESTful management endpoints (`/api/chat/sessions`, `/api/chat/{session_id}`, `/fork`, `/rewind`), and upgraded the frontend UI for seamless server-first session resumption and turn branching.

### Key Deliverables:

1. **Domain DTOs & Memory Protocol Seam (Ticket 01)**:
   - Defined `ConversationTurn` and `SessionInfo` domain models in `app/rag/memory/base.py`.
   - Defined `ConversationMemoryAdapter` protocol covering message hydration (`aget_messages`), turn retrieval (`aget_turns`), turn persistence (`asave_turn`), clearing (`aclear`), truncation (`atruncate_from`), branching (`afork_session`), and session indexing (`alist_sessions`, `aget_session`).
   - Decoupled memory persistence from graph-internal checkpointer blobs, standardizing storage on canonical `(HumanMessage, AIMessage)` turn pairs.

2. **In-Memory & No-Op Adapters (Ticket 02)**:
   - Implemented `InMemoryConversationStore` in `app/rag/memory/in_memory.py` with deep-copy isolation, safe turn slicing, session forking, and chronological turn indexing.
   - Implemented `NoOpConversationStore` in `app/rag/memory/noop.py` for stateless testing and disabled-memory configurations.
   - Verified 100% contract compliance across both adapters with comprehensive unit tests.

3. **SQLite Persistent Adapter with WAL & Lineage Tracking (Ticket 03)**:
   - Implemented `SQLiteConversationStore` in `app/rag/memory/sqlite.py` managing normalized `conversation_sessions` and `conversation_turns` relational tables.
   - Configured high-performance concurrency pragmas: `PRAGMA foreign_keys = ON;`, `PRAGMA journal_mode = WAL;`, and `PRAGMA busy_timeout = 5000;`.
   - Offloaded synchronous `sqlite3` execution to threadpools via Starlette's `run_in_threadpool` wrapped with an `asyncio.Lock` to guarantee event loop non-blocking.
   - Implemented atomic turn truncation, session deletion with cascading foreign keys, and recursive or indexed session forking.

4. **Workflow Topologies, Transient State Scrubbing & Query Condensation (Ticket 04)**:
   - Enabled conversational metadata (`is_conversational=True`) across fixed DAG and ReAct workflows.
   - Built query condensation (`condense_query`) in `SelfCorrectiveRagWorkflow`: resolves conversational context, pronouns, and references into a standalone query written to `state["current_query"]` while preserving `state["question"]` verbatim.
   - Implemented 0ms zero-overhead passthrough for single-turn inquiries (`len(messages) <= 1`).
   - Scrubbed transient retrieval artifacts (`kb_docs`, `web_results`, `retry_count`, `citations`, `retrieved_contexts`, `trace`) on each new turn so artifacts from prior turns never bleed into subsequent turns.

5. **WorkflowRunner Memory Orchestration & Safe Rewind (Ticket 05)**:
   - Built memory hydration into `WorkflowRunner._prepare_execution`: loads bounded history (`max_turns`) from server memory when `session_id` is supplied.
   - Implemented atomic post-execution turn saving in `WorkflowRunner._finalize_execution` for both batch `aexecute` and streaming `astream_events` flows.
   - Designed the **Safe Rewind Commit Pattern**: when `rewind_turn_index = k` is requested, history is loaded up to `[0 .. k-1]` without deleting subsequent turns. Truncation (`atruncate_from`) is committed atomically alongside the new turn only after successful LLM generation.
   - Handled client disconnects and unhandled exceptions gracefully by aborting turn persistence.

6. **RESTful API Transport Endpoints (Ticket 06)**:
   - Added `/api/chat/sessions`, `/api/chat/{session_id}`, `/api/chat/{session_id}/rewind`, and `/api/chat/{session_id}/fork` endpoints in `app/api/routes.py`.
   - Built transport validation in `ChatRequest.sanitize_session_id` to sanitize sentinel strings.
   - decupled route schemas from internal memory representations via `SessionDetailResponse` and `SessionListResponse`.

7. **Frontend Server-First Resumption & Branching UI (Ticket 07)**:
   - Implemented server-first session synchronization, switching, and turn hydration in `static/js/app.js`.
   - Added turn edit buttons and dynamic action banners ("Rewind Here" / "Fork to New Branch").
   - Integrated cache-busting, resilient DOM bootstrap checking `document.readyState`, and `DOMPurify` whitelist protection.

---

## 2. What Went Well

1. **Clean Domain Protocol Seam (`ConversationMemoryAdapter`)**:
   - Defining a standalone domain protocol independent from LangGraph checkpointers allowed complete decoupling between workflow runtime and storage engines.
   - Swapping `InMemoryConversationStore` into tests and `SQLiteConversationStore` into production required zero shims or adapter glue code.
   - Conformance was verified across 100% of methods in both synchronous and asynchronous contexts.

2. **Strict TDD Discipline Across 653 Passing Tests**:
   - Every single component—from SQLite transaction management to query condensation routing and SSE streaming rewind—was developed test-first.
   - All 653 tests pass with 0 failures, 100% offline capability, and zero external network calls.
   - Static type analysis with Pyright ran with 0 errors across `app/rag/memory/`, `app/rag/workflows/runner.py`, and `app/api/routes.py`.

3. **Safe Rewind Commit Pattern**:
   - Decoupling history loading from store mutation during rewind prevented irrevocable data loss when an edit query failed midway through generation.
   - Users can safely retry or cancel turn edits without leaving the database in an inconsistent or prematurely truncated state.

4. **Zero Transport Leakage**:
   - `ConversationMemoryAdapter` and `WorkflowRunner` operate strictly on domain primitives (`BaseMessage`, `ConversationTurn`, `SessionInfo`, `WorkflowInput`, `WorkflowOutput`).
   - Transport schemas remain strictly confined to `app/api/routes.py` and `app/api/schemas.py`.

---

## 3. Friction Points & Challenges Encountered

1. **Headless/Mocked JS Tests vs. Real Browser Execution**:
   - *Problem*: In headless Node/JSDOM tests, DOM elements were mocked with simple objects, passing unit tests. However, in a real browser:
     - Inserting dynamic action banners via `form.parentNode.insertBefore(banner, form)` caused visual breakage or misplaced DOM nodes.
     - `DOMPurify.sanitize()` stripped out custom `data-turn-index` and `data-query` attributes from button elements, causing event delegation to silently fail.
     - Browsers aggressively cached `/static/js/app.js` and `/static/css/style.css`, serving stale logic even after server-side updates.
     - Direct execution of bootstrap scripts failed when scripts were loaded asynchronously after the DOM had already transitioned to `complete` or `interactive`.
   - *Resolution*:
     - Added an explicit `<div id="turnActionContainer">` mount point in `templates/index.html`.
     - Whitelisted `data-turn-index` and `data-query` in `PURIFY_CONFIG.ADD_ATTR`.
     - Added versioned query parameters (`?v=mem1`) to asset tags in `templates/index.html`.
     - Updated script bootstrap to inspect `document.readyState` before binding `DOMContentLoaded`.

2. **Session Identity Sanitization & Sentinels**:
   - *Problem*: Client code occasionally stored `"undefined"` or `"null"` as string literals in `localStorage`, which were then sent as HTTP headers or JSON fields (`session_id: "undefined"`). The server treated `"undefined"` as a valid session identifier, creating orphaned database records.
   - *Resolution*:
     - Implemented `ChatRequest.sanitize_session_id` using a Pydantic `@field_validator(mode="before")` to strip whitespace and convert `"undefined"`, `"null"`, and empty strings into `None`.
     - Added defensive checks in `WorkflowRunner._ahydrate_memory` and `_apersist_turn`.
     - Sanitized `localStorage` lookups in `app.js` during initialization.

3. **In-Place Mutation of Domain Objects vs. Immutable Data Flow**:
   - *Problem*: In early iterations of memory hydration, `WorkflowRunner._ahydrate_memory` mutated the caller's `request.messages` in-place (`request.messages = hydrated`). When a workflow also utilized an internal checkpointer (such as LangGraph's `MemorySaver`), the graph received Turn 1 from both `request.messages` and the checkpointer, resulting in duplicate history.
   - *Resolution*:
     - Maintained clear separation between checkpointer workflows and stateless workflows in `to_initial_state`.
     - Codified the architectural rule that domain inputs must be treated immutably, deriving initial state down the call stack rather than mutating `WorkflowInput` in-place.

---

## 4. Codified Agent Guardrails & Updated Rules

The following rules have been codified into `CODING_STANDARDS.md` and `docs/agents/conversation-memory.md`:

1. **Safe Rewind & Atomic Truncation**:
   - Never pre-emptively truncate or delete historical turns upon receiving an edit request. Defer truncation until generation completes successfully.
2. **Session Identity Sanitization**:
   - Never allow sentinel string literals (`"undefined"`, `"null"`, whitespace) to pass as valid session IDs. Sanitize at transport boundaries and client storage.
3. **Immutable Domain Data Flow**:
   - Domain DTOs (`WorkflowInput`) are immutable. Do not mutate caller inputs in-place during pipeline execution.
4. **Browser DOM Execution Guardrails**:
   - Always provide dedicated container mount elements for dynamic UI controls.
   - Explicitly configure `DOMPurify.ADD_ATTR` to retain custom `data-*` attributes required for event delegation.
   - Version static assets with query-string cache-busters (`?v=...`).
   - Handle `document.readyState` in client bootstraps to avoid missed `DOMContentLoaded` events.

---

## 5. Artifact & Ticket Archive Status

All 7 work packages have been archived to `.scratch/archive/0016-unified-pluggable-conversation-memory-and-session-branching/issues/`:

| Ticket | Scope | Status |
|---|---|---|
| **Ticket 01** | Domain DTOs (`ConversationTurn`, `SessionInfo`) & Memory Protocol Seam | Completed & Archived |
| **Ticket 02** | In-Memory & No-Op Conversation Adapters | Completed & Archived |
| **Ticket 03** | SQLite Persistent Conversation Adapter with WAL Mode | Completed & Archived |
| **Ticket 04** | Workflow Topologies, Transient State Scrubbing & Query Condensation | Completed & Archived |
| **Ticket 05** | WorkflowRunner Memory Orchestration & Safe Rewind Pattern | Completed & Archived |
| **Ticket 06** | RESTful Transport Endpoints (`/sessions`, `/rewind`, `/fork`) | Completed & Archived |
| **Ticket 07** | Frontend Server-First Resumption, Switching & Branching UI | Completed & Archived |

Active scratch tickets have been removed from `.scratch/tickets/` and consolidated into the archive.

---

## 6. Future Architectural Opportunities

1. **Automatic Session Summarization & Compaction**:
   - For conversations exceeding `max_turns` (e.g. > 10 turns), execute an asynchronous background summarization task to compress older turns into a cohesive rolling summary turn, preserving long-term semantic context without token overflow.
2. **Distributed / Enterprise Memory Adapters**:
   - Implement a PostgreSQL/pgvector-backed or Redis-backed `ConversationMemoryAdapter` for horizontal multi-replica deployments.
3. **Lineage Graph Visualizer**:
   - Extend the frontend sidebar with an interactive visual DAG tree showing session forks and branch points.
