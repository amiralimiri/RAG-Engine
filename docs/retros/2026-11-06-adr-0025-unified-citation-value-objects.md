# Retrospective: Milestone 0025 / ADR-0025 (Unified End-to-End Citation Value Objects Across Memory and Transport Seams)

**Milestone:** ADR-0025 (Tickets 01–04 + Code Review Polish `79deca7`)  
**Date:** November 6, 2026  
**Status:** Completed, Reviewed, & Verified  
**ADR Reference:** `docs/adr/0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams.md`  
**Specification:** `docs/specs/0025-unified-end-to-end-citation-value-objects-across-memory-and-transport-seams-spec.md`  
**Work Packages / Tickets:** `docs/specs/0025-unified-end-to-end-citation-value-objects/issues/` (Tickets 01–04)  
**Domain Guidelines:** `docs/agents/conversation-memory.md` | `docs/agents/tool-abstractions.md` | `docs/agents/codebase-unification.md` | `CODING_STANDARDS.md`  
**Test Baseline:** 932 passed across entire test suite, zero ruff errors, clean pyright type checks across all touched subsystems.

---

## 1. Executive Summary & Major Accomplishments

Milestone 0025 established a unified, strongly-typed `Citation` value object spanning the entire lifecycle of query execution: from tool retrieval artifacts through agent graph state, workflow execution outputs, persistent conversation memory adapters, streaming SSE events, and evaluation listener telemetry.

Previously:
1. **Layer Inversion Risk**: `Citation` resided inside `app/rag/tools/base.py`. Adopting it in memory adapters or presentation schemas would have caused high-level subsystems to invert dependencies by importing from the tool execution layer.
2. **Object Degradation into Raw Dicts**: Workflows (`engine.py`, `react_agent.py`) converted structured `Citation` domain objects into raw dictionaries via `.model_dump()` to satisfy `AgentState["citations"] = list[dict[str, Any]]`.
3. **Transport Wire Fragility**: API schemas (`ChatResponse`, `StreamMetadataEvent`) and SSE serialization relied on ad-hoc dict unpacking, duck-typed access (`c.get("title")`), or custom `_serialize_citation` transformers.
4. **Memory Loss of Typing**: `ConversationTurn.citations` and SQLite persistence converted citations to unstructured JSON arrays without domain deserialization, forcing downstream consumers to re-parse untyped dictionaries.
5. **Evaluation Heuristic Scraping**: Online evaluation listeners inspected citation dict keys (`"context"`, `"page_content"`, `"snippet"`) to guess retrieved evidence contexts.

### Key Deliverables:
- **Canonical Relocation to Domain Core (`app/rag/state.py`)**:
  - Relocated `Citation` (`title: str`, `url: str`, `type: str`) to `app/rag/state.py` alongside foundational domain models.
  - Defined `CitationList = Annotated[list[Citation], BeforeValidator(coerce_citations)]` for uniform, reusable boundary validation across all Pydantic schemas.
- **End-to-End Typed State & Graph Seams**:
  - Updated `AgentState["citations"]` to `Sequence[Citation]` across both the 10-node DAG and ReAct agent graphs.
  - Eliminated `.model_dump()` degradation: tool-derived `Citation` instances are passed directly into workflow states without intermediate dictionary decay.
- **Strongly-Typed Memory Adapters & SQLite Serialization**:
  - Standardized `ConversationMemoryAdapter`, `ConversationTurn`, `InMemoryConversationStore`, `NoOpConversationStore`, and `SQLiteConversationStore` on `Sequence[Citation]` and `list[Citation]`.
  - Implemented typed SQLite deserialization via `coerce_citations(json.loads(row["citations_json"]))` and extracted reusable `_dump_citations_json` helper.
- **Transport Schemas & SSE Serialization**:
  - Standardized `ChatResponse`, `StreamMetadataEvent`, and `ChatTurnResponse` on `CitationList`.
  - Pruned custom `_serialize_citation` function from `app/api/streaming.py`; SSE events now serialize `Citation.model_dump()` directly.
- **Evaluation Seam Alignment**:
  - Removed citation dictionary context scraping heuristics from `OnlineEvaluationListener._extract_contexts`, maintaining clean separation between context strings and reference citations.
- **Zero-Shim Pruning & Code Review Polish (`79deca7`)**:
  - Replaced backward-compatibility re-exports in `app/rag/tools/base.py` and `app/rag/tools/__init__.py` with direct imports from `app.rag.state`.
  - Enforced strict dict validation at Pydantic model boundaries (`Citation(**data)`).

---

## 2. Work Package Breakdown & Ticket Completion

| Ticket | Summary | Primary Modules | Status |
| :--- | :--- | :--- | :--- |
| **Ticket 01** | Relocate Citation Domain Model, Boundary Coercion & Zero-Shim Invariants | `app/rag/state.py`, `app/rag/tools/base.py`, `tests/rag/test_citation_domain.py` | Closed |
| **Ticket 02** | End-to-End Workflow Engine & Agent Graph State Typing | `app/rag/state.py`, `app/rag/engine.py`, `app/rag/workflows/react_agent.py`, `app/rag/workflows/self_corrective.py` | Closed |
| **Ticket 03** | Strongly-Typed Conversation Memory Storage & SQLite Deserialization | `app/rag/memory/base.py`, `app/rag/memory/in_memory.py`, `app/rag/memory/sqlite.py`, memory test suites | Closed |
| **Ticket 04** | Transport Schemas, Streaming SSE Emission & Evaluation Seam Alignment | `app/api/schemas.py`, `app/api/streaming.py`, `app/rag/workflows/listeners.py`, API & SSE test suites | Closed |
| **Polish** | Strict Dict Validation, Tool Re-export Pruning & DRY SQLite Serialization | `app/rag/state.py`, `app/rag/memory/sqlite.py`, `app/rag/tools/`, `app/rag/workflows/base.py` | Closed (`79deca7`) |

---

## 3. What Went Well

1. **Reusable Boundary Coercion with `CitationList`**:
   - Creating `CitationList = Annotated[list[Citation], BeforeValidator(coerce_citations)]` allowed five different Pydantic models across presentation, workflow, and memory layers (`WorkflowOutput`, `ConversationTurn`, `ChatResponse`, `StreamMetadataEvent`, `ChatTurnResponse`) to share identical coercion logic without duplicating validator methods or boilerplate.
2. **Layered Architectural Cleanliness**:
   - Relocating `Citation` to `app/rag/state.py` resolved the risk of dependency inversion. Now presentation (`app/api`), persistence (`app/rag/memory`), workflow execution (`app/rag/workflows`), and tool abstractions (`app/rag/tools`) depend inward on domain state primitives, adhering to clean architecture principles.
3. **Robust Regression Suite Resilience**:
   - Despite touching the core state across the entire engine and memory persistence layers, all 932 tests passed green without breaking backward compatibility for shorthand string citations or legacy dictionary inputs.
4. **Actionable Two-Axis Code Review (`/code-review`)**:
   - The parallel Standards and Spec review surfaced real subtle issues prior to milestone closing:
     - The Spec reviewer identified that `coerce_citations` was silently dropping dictionaries lacking `title`/`source` instead of letting Pydantic reject them.
     - The Standards reviewer flagged the lingering pass-through re-export in `tools/base.py` and duplicate JSON serialization in `sqlite.py`.
   - Resolving these in a dedicated polish commit ensured zero technical debt was left behind.

---

## 4. Friction Points & Lessons Learned

### 4.1. Ambiguity Between Backward-Compatibility Specs vs. Zero-Shim Standards
- **Symptom:** The spec and ADR text originally suggested: *"Re-export `Citation` from `app/rag/tools/base.py` and `app/rag/tools/__init__.py` for complete backward compatibility."* However, `CODING_STANDARDS.md` and `codebase-unification.md` explicitly enforce Zero-Shim architecture (*"Do not mock pass-through re-exports; patch authoritative origin seams"*).
- **Root Cause:** Spec authoring prioritized transitional developer convenience without recognizing that pass-through re-exports are shims that accumulate technical debt.
- **Lesson / Action:** ADRs and ticket specs should prescribe direct migration of internal callers rather than specifying re-export shims. When a symbol moves, all internal references should migrate in the same ticket or polish phase.

### 4.2. Silent Fallbacks vs. Fail-Fast Validation
- **Symptom:** In Ticket 01, `coerce_citations` returned `None` when a dictionary did not possess a `title` or `source` key, silently dropping the invalid citation item.
- **Root Cause:** A defensive programming habit to prevent runtime exceptions masked invalid data payloads.
- **Lesson / Action:** Boundary validators in Pydantic should normalize well-formed legacy shapes (e.g. mapping `source` -> `title`), but delegate unknown structures directly to the constructor (`Citation(**item)`) so Pydantic's `ValidationError` can report the exact schema violation.

### 4.3. Windows CRLF Line Endings in File Edits
- **Symptom:** String matching tools encountered failures when replacing code blocks across files containing Windows CRLF (`\r\n`) newlines.
- **Root Cause:** Mismatch between Unix LF string literals and file system CRLF encoding.
- **Lesson / Action:** Utilize PowerShell or Python scripts with newline-insensitive reading/writing when editing files with heterogeneous line endings.

---

## 5. Codified Architectural Guardrails

1. **Domain Primitives Belong in Core State**:
   - Domain value objects shared across multiple subsystems (e.g. `Citation`, `RouteDecision`) must live in `app/rag/state.py` or domain models, never inside peripheral subsystems like tools, persistence adapters, or transport schemas.
2. **Standardized Pydantic Boundary Coercion**:
   - Use `Annotated[..., BeforeValidator(...)]` type aliases when the same coercion policy applies across multiple domain or transport schemas.
3. **Fail-Fast Boundary Parsing**:
   - Never silently swallow or drop invalid dictionary items in Pydantic `BeforeValidator` functions. Normalize recognized aliases and pass through to the model constructor to trigger standard validation errors.
4. **Zero-Shim Relocations**:
   - When relocating symbols to an authoritative origin, migrate internal callers and do not leave pass-through re-exports behind in legacy modules.
