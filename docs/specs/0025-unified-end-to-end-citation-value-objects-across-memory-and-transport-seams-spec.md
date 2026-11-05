# Technical Specification: Unified End-to-End Citation Value Objects Across Memory and Transport Seams

- **ADR Reference:** `docs/adr/0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams.md` (extends ADR-0024)
- **Target Subsystems:** `app/rag/state.py`, `app/rag/tools/`, `app/rag/engine.py`, `app/rag/workflows/`, `app/rag/memory/`, `app/api/`, `tests/`
- **Specification Document:** `docs/spec.md` & `docs/specs/0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams-spec.md`
- **Status:** Proposed / Ready for Review
- **Triage Label:** `ready-for-agent`

---

## Problem Statement

Following the introduction of the strongly-typed `Citation` value object in ADR-0024, our tool execution layer reliably emits typed citation metadata. However, downstream subsystems across the codebase immediately degrade these typed models back into ad-hoc dictionaries:

1. **Pervasive Serialization Shims Across Internal Seams**:
   Fixed DAG nodes (`app/rag/engine.py`) and autonomous agent workflows (`app/rag/workflows/react_agent.py`) immediately degrade `Citation` objects into dictionaries via `.model_dump()` before storing them in `AgentState["citations"]` or assigning them to `WorkflowOutput.citations`. Callers and downstream listeners are forced to parse heterogeneous dictionary payloads.
2. **Layer Inversion & Subsystem Coupling Risk**:
   Because `Citation` was defined in `app/rag/tools/base.py`, adopting it across the conversation memory subsystem (`app/rag/memory/base.py`) or transport API models (`app/api/schemas.py`) would force memory and presentation layers to import from the tool execution subsystem. Subsystems managing conversation persistence and network transport should not couple to tool execution packages.
3. **Ad-Hoc Presentation Shims**:
   The SSE streaming adapter (`app/api/streaming.py`) maintains an ad-hoc helper (`_serialize_citation`) to dynamically inspect whether citations are Pydantic models or dictionaries during event framing.
4. **Heuristic Context Scraping in Evaluation**:
   `OnlineEvaluationListener._extract_contexts` in `app/rag/workflows/listeners.py` contains a legacy heuristic searching for `"content"` or `"snippet"` keys in citation dictionaries, conflating reference attribution (`title`, `url`, `type`) with retrieved context text.
5. **Runtime Dict Duck-Typing vs. Zero-Shim Standards**:
   Callers, assertions, and tests oscillate between attribute access (`c.title`) and dictionary indexing (`c["title"]`), violating the repository's Zero-Shim architecture standard.

---

## Solution

Unify the `Citation` value object end-to-end across workflow state, memory persistence, and transport presentation without intermediate dictionary degradation:

1. **Foundational State Domain Placement**:
   Relocate the canonical `Citation` model definition to `app/rag/state.py` alongside foundational domain models (`RouteDecision`, `EvidenceGrade`). Re-export `Citation` from `app/rag/tools/` and `app/rag/tools/base.py` for complete backward compatibility.
2. **Workflow Graph State Invariant**:
   Declare `AgentState["citations"]` as `Sequence[Citation]` in `app/rag/state.py`. Maintain `list[Citation]` throughout LangGraph nodes, deleting all `.model_dump()` degradation shims in `engine.py`, `self_corrective.py`, and `react_agent.py`.
3. **Strongly-Typed Memory Contract**:
   Type `ConversationTurn.citations` strictly as `list[Citation] = Field(default_factory=list)`. Update `aappend_turn(..., citations=...)` across all memory adapters (`SQLiteConversationStore`, `InMemoryConversationStore`, `NoOpMemoryStore`). Deserialization in `_row_to_turn` hydrates validated `Citation` instances from JSON storage.
4. **Transport & Presentation Schema Alignment**:
   Standardize `WorkflowOutput`, `ChatResponse`, `StreamMetadataEvent`, and `ChatTurnResponse` on `citations: list[Citation] = Field(default_factory=list)`. Pydantic and FastAPI handle JSON serialization natively at the HTTP/SSE boundary, preserving exact wire compatibility.
5. **Strict Zero-Shim Contract**:
   `Citation` remains an immutable value object (`frozen=True`) with explicit typed attributes (`title`, `url`, `type`). No `__getitem__` or `.get()` dict shims will be added; callers and tests use standard attribute access.
6. **Boundary Coercion Validator**:
   Implement a shared Pydantic `field_validator("citations", mode="before")` (`_coerce_citations`) on `WorkflowOutput`, `ConversationTurn`, and `ChatResponse` to coerce legacy dictionary payloads (`{"title": ...}`) and shorthand strings (`"doc.pdf"`) into validated `Citation` instances at the model boundary.
7. **Pruning Shims and Heuristics**:
   Remove `_serialize_citation` from `app/api/streaming.py`. Prune the citation dictionary inspection heuristic from `OnlineEvaluationListener._extract_contexts`, establishing `output.retrieved_contexts` as the single source of truth for evaluation evidence.

---

## User Stories

1. As a workflow developer, I want `AgentState["citations"]` and `WorkflowOutput.citations` to contain strongly-typed `Citation` objects, so that I can access citation fields with auto-completion and static type safety without dictionary key lookups.
2. As a presentation layer developer, I want `ChatResponse`, `StreamMetadataEvent`, and `ChatTurnResponse` to declare `list[Citation]`, so that Pydantic and FastAPI handle serialization to the wire protocol natively without ad-hoc helper shims.
3. As a database maintainer, I want `ConversationTurn.citations` to hydrate as validated `Citation` instances when loaded from SQLite, so that conversation history maintains the exact same domain contract as live workflow outputs.
4. As an engineer writing tests for workflows, I want `WorkflowOutput` to accept string shorthand citations (e.g. `["vpn_guide.md"]`) via boundary coercion, so that unit test fixtures remain clean without verbose model instantiation.
5. As an engineer reviewing codebase dependencies, I want `app/rag/memory/` and `app/api/` to import `Citation` from `app/rag/state.py`, so that neither memory nor presentation layers depend on tool execution modules.
6. As a maintainer of the streaming pipeline, I want `SseStreamAdapter` to serialize `StreamMetadataEvent` using standard `model_dump(mode="json")`, so that defensive object-type checking functions (`_serialize_citation`) can be permanently deleted.
7. As an online evaluation maintainer, I want `OnlineEvaluationListener` to extract contexts strictly from `output.retrieved_contexts`, so that citation attribution models are not conflated with document retrieval text.
8. As a code reviewer, I want `Citation` to adhere to the Zero-Shim principle without dict-like `__getitem__` shims, so that all consumers consistently use attribute access (`c.title`, `c.url`, `c.type`).
9. As a developer adding new retrieval tools, I want my tool's `Citation` outputs to flow unaltered through the workflow engine, checkpointer, and memory store directly to API consumers, so that no intermediate subsystem drops or alters citation attributes.
10. As an IT support API consumer, I want JSON responses from `/api/chat`, `/api/chat/stream`, and `/api/chat/{session_id}` to preserve identical `{ "title": "...", "url": "...", "type": "..." }` schemas, so that client frontend rendering continues without modification.
11. As an engineer testing conversation rewind, I want `aappend_turn(citations=[Citation(...)])` to atomically persist and truncate history with typed citations, so that turn rewinding maintains citation integrity.
12. As a developer running benchmark experiments, I want evaluation runs to grade responses using typed outputs, so that test double assertions do not fail on dictionary discrepancies.

---

## Implementation Decisions

### 1. Domain Placement and Re-Exports
- Relocate `Citation` model definition to `app/rag/state.py`.
- Re-export `Citation` from `app/rag/tools/base.py` and `app/rag/tools/__init__.py`.
- `Citation` remains an immutable model:
  ```python
  class Citation(BaseModel):
      """Strongly-typed reference citation attributed to retrieved evidence."""

      model_config = ConfigDict(frozen=True)

      title: str
      url: str = ""
      type: str = "general"
  ```
- Subscripting (`__getitem__`) and `.get()` are deliberately omitted to uphold Zero-Shim standards.

### 2. Boundary Coercion Validator (`_coerce_citations`)
- Implement a reusable before-validator function for Pydantic models:
  ```python
  def _coerce_citations(val: Any) -> list[Citation]:
      if not val:
          return []
      if isinstance(val, (list, tuple, set)):
          coerced: list[Citation] = []
          for item in val:
              if isinstance(item, Citation):
                  coerced.append(item)
              elif isinstance(item, dict):
                  coerced.append(Citation(**item))
              elif isinstance(item, str) and item.strip():
                  coerced.append(Citation(title=item.strip()))
              elif hasattr(item, "model_dump"):
                  coerced.append(Citation(**item.model_dump()))
          return coerced
      if isinstance(val, Citation):
          return [val]
      if isinstance(val, dict):
          return [Citation(**val)]
      if isinstance(val, str) and val.strip():
          return [Citation(title=val.strip())]
      return []
  ```
- Apply `@field_validator("citations", mode="before")` using this function across `WorkflowOutput`, `ConversationTurn`, and `ChatResponse`.

### 3. Workflow Engine and State Integration
- In `app/rag/state.py`, update `AgentState`:
  ```python
  class AgentState(TypedDict):
      ...
      citations: Sequence[Citation]
  ```
- In `app/rag/engine.py`:
  - `node_retrieve_kb`: update `citations = list(result.artifact.citations)` (removing `[c.model_dump() ...]`).
  - `insufficient`: return `"citations": []`.
  - `direct_answer`: return `"citations": []`.
- In `app/rag/workflows/self_corrective.py`:
  - In `to_workflow_output`: pass `citations=list(final_state.get("citations") or [])` directly to `WorkflowOutput`.
- In `app/rag/workflows/react_agent.py`:
  - In `to_workflow_output`: set `citations = list(evidence.citations)` directly without `.model_dump()`.

### 4. Conversation Memory Integration
- In `app/rag/memory/base.py`:
  - `ConversationTurn.citations: list[Citation] = Field(default_factory=list)`.
  - `ConversationMemoryAdapter.aappend_turn`: update parameter to `citations: list[Citation] | None = None`.
- In `app/rag/memory/sqlite.py`:
  - In `_row_to_turn`: deserialize JSON text into `Citation` instances:
    ```python
    raw_citations = json.loads(row["citations_json"]) if row["citations_json"] else []
    citations = [Citation.model_validate(c) if isinstance(c, dict) else Citation(title=str(c)) for c in raw_citations]
    ```
  - In `aappend_turn`: serialize `citations_json = json.dumps([c.model_dump() for c in citations_clean]) if citations_clean else None`.
- In `app/rag/memory/in_memory.py`:
  - `aappend_turn` and `asave_turn` store `ConversationTurn` instances with `list[Citation]`.

### 5. API Schemas and Streaming Adapter Alignment
- In `app/api/schemas.py`:
  - `ChatResponse.citations: list[Citation] = Field(default_factory=list)`
  - `StreamMetadataEvent.citations: list[Citation] = Field(default_factory=list)`
  - `ChatTurnResponse.citations: list[Citation] = Field(default_factory=list)`
- In `app/api/streaming.py`:
  - Delete `_serialize_citation`.
  - In `SseStreamAdapter.format`: pass `metadata_payload["citations"] = list(output.citations or [])`.

### 6. Evaluation Listener Purification
- In `app/rag/workflows/listeners.py`:
  - In `OnlineEvaluationListener._extract_contexts`: prune the fallback that iterates over `output.citations` looking for dict keys `"content"` or `"snippet"`. Return `list(output.retrieved_contexts)` or call `_context_extractor(output)`.

---

## Testing Decisions

### Good Test Principles
- **Test External Behavior & Contracts**: Tests verify that API routes, streaming SSE events, memory stores, and workflow runners emit and accept valid citations without inspecting internal private flags.
- **Strict Attribute Access**: Update all test assertions across the test suite from `citation["title"]` to `citation.title`.
- **Coercion Boundary Tests**: Test that `WorkflowOutput`, `ConversationTurn`, and `ChatResponse` properly coerce strings, dicts, and `Citation` objects into identical `list[Citation]` structures.

### Target Modules Tested
- `tests/rag/test_tools.py`: Verify `Citation` re-exports and immutability.
- `tests/rag/test_workflow_engine.py` & `tests/rag/test_self_corrective_contract.py`: Verify engine nodes preserve `list[Citation]`.
- `tests/rag/test_react_agent.py`: Verify ReAct agent workflow outputs `list[Citation]`.
- `tests/rag/test_conversation_memory_sqlite.py` & `in_memory`: Verify serialization and hydration of `Citation` models across turns and forks.
- `tests/api/test_sse_stream_adapter.py`: Verify SSE metadata frames serialize citations cleanly without `_serialize_citation`.
- `tests/api/test_routes_multi_workflow.py` & `test_routes_chat_memory.py`: Verify HTTP JSON output compatibility.
- `tests/test_schemas.py`: Verify schema validation and coercion behavior.

### Prior Art
- ADR-0024 Tool abstraction tests (`tests/rag/test_tools.py`)
- ADR-0023 Conversation memory aggregate tests (`tests/rag/test_conversation_memory_sqlite.py`)
- ADR-0022 Self-contained execution event streaming tests (`tests/api/test_sse_stream_adapter.py`)

---

## Out of Scope
- Adding new fields to `Citation` (e.g., page numbers, chunk IDs, confidence scores).
- Modifying vector store schemas or Pinecone metadata indexing.
- Changing frontend UI layout or client-side citation rendering components.
- Modifying tool execution or evidence aggregation logic (`aggregate_tool_evidence`).

---

## Further Notes
- This refactoring is purely an internal typing and boundary cleanup that deepens existing modules and eliminates runtime conversion shims.
- Wire-level API schemas remain 100% JSON-compatible with the frontend client.
