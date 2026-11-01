# Tickets: Unified Pluggable Conversation Memory and Session Branching Architecture

- **Specification Reference:** `docs/specs/0016-conversation-memory.md`
- **ADR Reference:** `docs/adr/0016-unified-pluggable-conversation-memory-and-session-branching.md`
- **Target Version:** `0.16.0`
- **Status:** Pending

---

## Ticket Overview

| Ticket | Title | Blocked By | Status |
| :--- | :--- | :--- | :--- |
| **01** | Domain DTOs, Memory Protocol Seam & Configuration | None | Pending |
| **02** | In-Memory & NoOp Memory Adapters with Factory Entrypoint | 01 | Pending |
| **03** | Durable SQLite Persistence Adapter with WAL & Lineage Tracking | 01, 02 | Pending |
| **04** | Workflow Topologies Multi-Turn State Hygiene, Query Condensation & ReAct Decoupling | 01 | Pending |
| **05** | WorkflowRunner Execution Pipeline Memory Hydration, Safe Rewind & Persistence | 01, 02, 03, 04 | Pending |
| **06** | REST API Schemas & Endpoints (Sessions List, History, Delete, Rewind, Fork) | 01, 02, 03, 05 | Pending |
| **07** | Frontend Client Server-First Resumption, Session Switching, & Turn Edit/Fork UI | 06 | Pending |

---

## Detailed Ticket Breakdown

The full bite-sized specifications for each ticket are stored in `.scratch/tickets/`:

- `.scratch/tickets/01-domain-dtos-and-memory-protocol.md`:
  Defines `ConversationTurn`, `SessionInfo`, and `@runtime_checkable` `ConversationMemoryAdapter` in `app/rag/memory/base.py`, plus memory configuration in `app/core/config.py`.
- `.scratch/tickets/02-in-memory-and-noop-adapters.md`:
  Implements `InMemoryConversationStore` (thread-safe, bounded, test double) and `NoOpMemoryStore` in `app/rag/memory/`, with factory entrypoint `get_conversation_memory()`.
- `.scratch/tickets/03-sqlite-persistent-adapter.md`:
  Implements `SQLiteConversationStore` with standard library `sqlite3` in WAL mode, foreign keys, cascading deletes, and lineage tracking.
- `.scratch/tickets/04-workflow-topologies-and-condensation.md`:
  Extends `AgentState` with `messages: Annotated[list[BaseMessage], operator.add]`, enforces per-turn transient state scrubbing, adds `condense_query` node to the 10-node DAG, injects history into generation prompts, and decouples `ReActAgentWorkflow` from internal checkpointers.
- `.scratch/tickets/05-workflow-runner-memory-orchestration.md`:
  Injects memory into `WorkflowRunner`, orchestrates pre-flight hydration in `_prepare_execution`, safe pre-execution slice for rewind, and atomic post-execution turn persistence and safe truncation in `_finalize_execution`.
- `.scratch/tickets/06-api-transport-endpoints.md`:
  Implements `ChatTurnResponse`, `SessionDetailResponse`, `SessionListResponse`, `RewindRequest`, `ForkRequest`, and route handlers for `/api/chat/sessions`, `/api/chat/{session_id}`, rewind, and fork.
- `.scratch/tickets/07-frontend-resumption-and-branching-ui.md`:
  Implements server-first session resumption and switching, turn editing with an active `#turnActionBanner`, "Edit & Rewind Here", and "Branch to New Chat" in `static/js/app.js`.
