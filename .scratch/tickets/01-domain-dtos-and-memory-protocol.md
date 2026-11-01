# Ticket 01: Domain DTOs, Memory Protocol Seam & Configuration

- **Milestone:** Unified Pluggable Conversation Memory (ADR-0016)
- **Status:** Completed
- **Dependency:** None (Foundational)
- **Estimated Scope:** Bite-sized

---

## 1. Goal & Context
Establish the foundation of the memory subsystem:
1. Define the strongly-typed domain DTOs (`ConversationTurn`, `SessionInfo`) and structural protocol seam (`ConversationMemoryAdapter`) in `app/rag/memory/base.py`.
2. Add conversational memory configuration settings (`conversation_memory_backend`, `conversation_db_path`, `memory_max_turns`) to `Settings` in `app/core/config.py`.

Domain modules must remain 100% decoupled from transport schemas (`app/api/schemas.py`) and from LangGraph checkpointer internals (`BaseCheckpointSaver`).

---

## 2. Technical Requirements

### 2.1 Domain Models (`app/rag/memory/base.py`)
- `ConversationTurn`:
  - `session_id: str`
  - `turn_index: int` (ge=0)
  - `user_query: str`
  - `assistant_answer: str`
  - `attachments: list[dict[str, Any]] = Field(default_factory=list)`
  - `citations: list[dict[str, Any]] = Field(default_factory=list)`
  - `source_used: str = Field(default="direct")`
  - `trace_id: str | None = None`
  - `created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))`
  - Helper method `to_messages() -> list[BaseMessage]`: Returns `[HumanMessage(content=self.user_query, additional_kwargs={"attachments": ...} if self.attachments else {}), AIMessage(content=self.assistant_answer)]`.
- `SessionInfo`:
  - `session_id: str`
  - `title: str`
  - `parent_session_id: str | None = None`
  - `forked_from_turn: int | None = None`
  - `created_at: datetime`
  - `updated_at: datetime`
  - `turn_count: int = 0`

### 2.2 Protocol Seam (`ConversationMemoryAdapter`)
- `@runtime_checkable` protocol with async signatures:
  - `async def aget_messages(self, session_id: str, limit: int | None = None) -> list[BaseMessage]: ...`
  - `async def aget_turns(self, session_id: str, limit: int | None = None) -> list[ConversationTurn]: ...`
  - `async def asave_turn(self, turn: ConversationTurn) -> None: ...`
  - `async def aclear(self, session_id: str) -> None: ...`
  - `async def atruncate_from(self, session_id: str, turn_index: int) -> None: ...`
  - `async def afork_session(self, source_session_id: str, upto_turn_index: int, new_session_id: str | None = None) -> str: ...`
  - `async def alist_sessions(self, limit: int = 50) -> list[SessionInfo]: ...`
  - `async def aget_session(self, session_id: str) -> SessionInfo | None: ...`

### 2.3 Configuration Settings (`app/core/config.py`)
- `conversation_memory_backend: Literal["sqlite", "in_memory", "noop"] = Field(default="sqlite")`
- `conversation_db_path: str = Field(default="data/conversations.db")`
- `memory_max_turns: int = Field(default=10, ge=1)`

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- Create `tests/rag/test_conversation_memory_base.py`:
  - `test_conversation_turn_creation_and_to_messages`: Validate Pydantic validation, serialization, and LangChain message projection with and without attachments.
  - `test_session_info_validation`: Validate defaults and lineage attributes (`parent_session_id`, `forked_from_turn`).
  - `test_protocol_runtime_checkable`: Define a dummy class satisfying all methods and verify `isinstance(dummy, ConversationMemoryAdapter) is True`. Verify an incomplete class returns `False`.
  - `test_config_settings`: Verify `conversation_memory_backend`, `conversation_db_path`, and `memory_max_turns` defaults and validation in `Settings`.

### Green (Implementation)
- Implement `app/rag/memory/base.py`.
- Update `app/core/config.py`.

### Refactor
- Ensure PEP 484 type compliance, clean docstrings, and strict decoupled imports (no imports from `app.api.*` or `langgraph.*`).
