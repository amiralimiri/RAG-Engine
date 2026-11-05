# ADR 0025: Unified End-to-End Citation Value Objects Across Memory and Transport Seams

## Status
Accepted

## Context
In ADR 0024, we introduced the strongly-typed `Citation` value object (`title: str`, `url: str`, `type: str`) to eliminate ad-hoc citation dictionaries in tool outputs. However, several architectural fractures remained across the broader system:
1. **Pervasive Dictionary Degradation Across Seams**: Both `app/rag/engine.py` and `app/rag/workflows/react_agent.py` immediately degraded `Citation` models back into dictionaries via `.model_dump()` before storing them in `AgentState["citations"]` or `WorkflowOutput.citations`.
2. **Layer Inversion Risk**: Because `Citation` was defined in `app/rag/tools/base.py`, adopting it across `ConversationMemoryAdapter` (`app/rag/memory/base.py`) and presentation schemas (`app/api/schemas.py`) would force memory and transport layers to depend on the tool execution subsystem, violating layered architectural boundaries.
3. **Ad-Hoc Serialization Shims**: `app/api/streaming.py` maintained a defensive helper function (`_serialize_citation`) to inspect and handle heterogeneous objects (dicts vs models) during SSE stream frame formatting.
4. **Heuristic Context Leakage**: `OnlineEvaluationListener._extract_contexts` in `app/rag/workflows/listeners.py` attempted to scrape document text from citation dictionaries looking for `"content"` or `"snippet"` keys, conflating citation reference metadata with retrieved context evidence.

---

## Decisions

### 1. Relocate `Citation` to Shared State Domain
- Move the canonical `Citation` model definition to `app/rag/state.py`, alongside core domain models (`RouteDecision`, `EvidenceGrade`).
- Re-export `Citation` from `app/rag/tools/` and `app/rag/tools/base.py` to preserve zero-friction backwards compatibility for existing tool callers.

### 2. End-to-End Typed Invariant in Workflows and Graph State
- Type `AgentState["citations"]` as `Sequence[Citation]` in `app/rag/state.py`.
- Preserve `list[Citation]` directly from `ToolResult.artifact.citations` across all LangGraph nodes (`node_retrieve_kb`, `node_generate_kb`, etc.), completely deleting `.model_dump()` degradation shims inside `engine.py` and `react_agent.py`.
- Type `WorkflowOutput.citations` strictly as `list[Citation] = Field(default_factory=list)`.

### 3. Strongly-Typed Conversation Memory Invariant
- Type `ConversationTurn.citations` strictly as `list[Citation] = Field(default_factory=list)` in `app/rag/memory/base.py`.
- Update `aappend_turn(citations=...)` signature on `ConversationMemoryAdapter` to accept `list[Citation] | None`.
- In `SQLiteConversationStore`, serialize citations as JSON arrays of dicts (`[c.model_dump() for c in citations]`), and deserialize rows back into validated `Citation` instances in `_row_to_turn`.

### 4. Transport & Presentation Schema Standardization
- Update `ChatResponse`, `StreamMetadataEvent`, and `ChatTurnResponse` in `app/api/schemas.py` to declare `citations: list[Citation] = Field(default_factory=list)`.
- Allow FastAPI and Pydantic to serialize `Citation` natively to JSON, ensuring 100% wire-protocol compatibility with web clients while enforcing strict types internally.

### 5. Strict Zero-Shim Contract on `Citation`
- In accordance with the Zero-Shim principle in `CODING_STANDARDS.md`, do **not** add dict-like `__getitem__` or `.get()` methods to `Citation`. Callers and tests must access typed attributes directly (`citation.title`, `citation.url`, `citation.type`).

### 6. Boundary Coercion Validator (`_coerce_citations`)
- Implement a shared Pydantic `field_validator("citations", mode="before")` on `WorkflowOutput`, `ConversationTurn`, and `ChatResponse` that coerces legacy dictionaries (`{"title": ...}`) and shorthand strings (`"doc.pdf"`) into validated `Citation` instances at the model boundary.

### 7. Prune Presentation Shims and Evaluation Heuristics
- Delete `_serialize_citation` from `app/api/streaming.py`; rely on `StreamMetadataEvent.model_dump(mode="json")` for SSE frame encoding.
- Prune the citation-inspection heuristic from `OnlineEvaluationListener._extract_contexts`, establishing `output.retrieved_contexts` (and `_context_extractor`) as the sole authoritative source for evaluation text contexts.

---

## Consequences

### Positive
- **Locality**: Citation validation, normalization, and defaults concentrate exclusively within the immutable `Citation` value object.
- **Leverage**: Every workflow, checkpointer/memory adapter, streaming adapter, and API route operates on the exact same strongly-typed contract without intermediary conversion shims.
- **Zero-Shim Adherence**: Eliminates runtime dict duck-typing, subscript shims, and defensive object serialization routines.
- **Clean Subsystem Boundaries**: Memory and presentation layers depend only on shared state domain contracts, not tool execution modules.

### Negative
- **Test Alignment**: Existing unit and integration tests that access citations as dictionaries (e.g. `c["title"]`) must be updated to use attribute access (`c.title`).
