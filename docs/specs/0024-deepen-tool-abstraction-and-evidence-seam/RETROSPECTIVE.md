# Retrospective: Milestone 0024 / ADR-0024 (Deepen Tool Abstraction and Evidence Seam)

**Milestone:** ADR-0024 (Tickets 01–03 + Polish)  
**Date:** November 5, 2026  
**Status:** Completed, Reviewed, & Verified  
**ADR Reference:** `docs/adr/0024-deepen-tool-abstraction-and-evidence-seam.md` (supersedes ADR-0008)  
**Specification:** `docs/specs/0024-deepen-tool-abstraction-and-evidence-seam-spec.md`  
**Work Packages / Tickets:** `docs/specs/0024-deepen-tool-abstraction-and-evidence-seam-tickets.md` (Tickets 01–03)  
**Domain Guidelines:** `docs/agents/tool-abstractions.md` | `CODING_STANDARDS.md` | `GLOSSARY.md`  
**Test Baseline:** 100% green across tool and workflow unit/contract suites, zero lint regressions (`ruff`).

---

## 1. Executive Summary & Major Accomplishments

Milestone 0024 resolved long-standing architectural friction in tool execution and evidence extraction across both fixed DAG workflows (`create_agentic_rag_graph`) and autonomous ReAct agents (`ReActAgentWorkflow`). The previous ADR-0008 implementation relied on `response_format="content_and_artifact"` alongside fragile hacks:

1. **Heterogeneous Dict Artifacts & Regex Fallbacks**: Tools returned ad-hoc dictionaries with conflicting keys (`{"documents": ..., "citations": ...}` vs `{"citations": ..., "retrieved_contexts": ...}`). In `ReActAgentWorkflow`, artifact type checking failed on every turn, silently falling back to regex scraping strings (`re.findall(r"\[Source:\s*([^\]]+)\]", ...)`).
2. **Global State & Monkey-Patching**: `DualOutputStructuredTool` stored `RunnableConfig` in a global `ContextVar` (`_TOOL_INVOKE_CONFIG`), passed synthetic `tool_call_id="__direct__"` into `super().run()`, and forced tools to recover config via `get_invoke_config()`.
3. **Workflow Coupling to Tool Identifiers**: `ReActAgentWorkflow` and `app/rag/engine.py` hardcoded specific tool names (`"knowledge_base_search"`, `"web_search"`) to determine `source_used` and deduplicate citations.
4. **Tuple Duck-Typing at Invocation Seams**: Fixed DAG nodes in `app/rag/engine.py` required a defensive `_unpack_tool_output` helper to inspect whether raw output was a tuple, an object with an `.artifact` attribute, or a raw string.

### Key Deliverables:

- **Canonical Domain Models (`app/rag/tools/base.py`)**:
  - `Citation`: Immutable domain model (`title: str`, `url: str`, `type: str`).
  - `ToolEvidenceArtifact`: Pure structured evidence artifact (`citations: list[Citation]`, `retrieved_contexts: list[str]`, `source_used: str`, `raw_payload: Any`). Free of dictionary-emulation shims (`__getitem__`, `get`).
  - `ToolResult`: High-leverage execution container for direct workflow callers with 2-tuple unpacking (`content, artifact = res`).
  - `AggregatedEvidence`: Immutable container holding deduplicated citations and contexts.
- **Deep Unified Execution Seam (`RAGTool`)**:
  - Replaced `DualOutputStructuredTool` with `RAGTool(StructuredTool)`.
  - Exposes explicit `await tool.aexecute(input, config=config)` and synchronous `.execute(input, config=config)` entrypoints.
  - Native LangChain config injection: `_run` and `_arun` accept `config: RunnableConfig | None = None` without global `ContextVar` state.
  - Autonomous ReAct agents (`create_agent` / LangGraph `ToolNode`) invoke standard `.ainvoke()`, emitting `ToolMessage` with `ToolEvidenceArtifact` on `.artifact`.
- **Dynamic Polymorphic Evidence Aggregator (`aggregate_tool_evidence`)**:
  - Automatically extracts `ToolEvidenceArtifact` instances from message history or artifact sequences.
  - Deduplicates citations by unique `(title, url)` pairs and preserves chronological context order.
  - Dynamically resolves `source_used`: `"direct"` if empty, single source if uniform, and `"react_agent"` if multiple distinct tools were executed.
- **In-Memory Deterministic Test Double (`FakeRAGTool`)**:
  - In-memory `FakeRAGTool(RAGTool)` double with configurable responses and full call inspection (`execute_calls`, `aexecute_calls`) for testing without network I/O.
- **Zero-Shim Pruning**:
  - Completely pruned `DualOutputStructuredTool`, `_TOOL_INVOKE_CONFIG`, `_UNSET`, `get_invoke_config()`, and `_unpack_tool_output`.

---

## 2. Work Package Breakdown & Ticket Completion

| Ticket | Summary | Scope | Status |
| :--- | :--- | :--- | :--- |
| **Ticket 01** | Canonical Domain Models & Polymorphic Evidence Aggregator | `app/rag/tools/base.py`, `tests/rag/test_tools.py` | Closed |
| **Ticket 02** | RAGTool Execution Seam, Concrete Adapters & Zero-Shim Pruning | `app/rag/tools/base.py`, `app/rag/tools/retriever.py`, `app/rag/tools/search.py`, `app/rag/tools/__init__.py`, `tests/rag/test_tools.py` | Closed |
| **Ticket 03** | Workflow Engine & ReAct Agent Integration with Regression Verification | `app/rag/engine.py`, `app/rag/workflows/react_agent.py`, `tests/rag/test_react_agent.py`, `tests/rag/test_base_workflow.py`, full regression suite | Closed |
| **Polish** | Zero-Shim Pruning (`__getitem__`/`get`), ReAct None Guard, Spans & Metadata Sync | `app/rag/tools/base.py`, `app/rag/workflows/react_agent.py`, `tests/test_retriever_search_spans.py`, ticket trackers | Closed |

---

## 3. What Went Well

1. **Locality & Deep Module Design**:
   - Provider clients, citation extraction, context formatting, and evidence aggregation now concentrate exclusively within `app/rag/tools/`. Graph nodes and workflow orchestrators are completely freed from parsing and scraping responsibilities.
2. **Polymorphic Tool Extensibility**:
   - Adding new tools (e.g. Jira, Confluence, GitHub) is now 100% plug-and-play. Workflows do not check tool names; evidence is aggregated polymorphically via `aggregate_tool_evidence()`.
3. **Pure Asynchronous Concurrency**:
   - Graph retrieval nodes (`node_retrieve_kb`, `node_search_web`) natively await `tool.aexecute()`, preventing threadpool blocking during streaming token generation.
4. **Ergonomic Call Contracts**:
   - `ToolResult`'s support for 2-tuple unpacking (`content, artifact = result`) preserved developer ergonomics while transitioning the internal architecture to strongly-typed models.
5. **Rigorous Code Review Gating**:
   - The parallel Standards and Spec `/code-review` successfully caught subtle issues: legacy dictionary emulation methods on `ToolEvidenceArtifact`, explicit `None` handling in `retrieved_contexts`, and test double adoption gaps.

---

## 4. Friction Points & Lessons Learned

### 4.1. Lingering Backward-Compatibility Shims
- **Symptom:** In Ticket 01, `ToolEvidenceArtifact` was initially provided with `__getitem__` and `.get()` methods under the intention of smoothing backward-compatible test assertions.
- **Root Cause:** A tendency to retain duck-typing shims during incremental ticket execution instead of enforcing the repo's strict Zero-Shim standard (ADR-0011).
- **Lesson / Action:** When introducing canonical domain models, avoid adding dict-emulation shims to domain classes. Update callers and test assertions directly to typed attribute access (`artifact.citations`).

### 4.2. Ad-Hoc Mocks vs. Canonical Test Doubles
- **Symptom:** During regression suite migration, several test files were updated by wiring ad-hoc `MagicMock(aexecute=...)` instances instead of adopting `FakeRAGTool`.
- **Root Cause:** Developers opted for quick inline mock patching rather than importing the canonical test double.
- **Lesson / Action:** Promote `FakeRAGTool` as the primary standard in `docs/agents/tool-abstractions.md`. Flag un-specced `MagicMock` instances during review.

### 4.3. Source Tag Semantic Alignment
- **Symptom:** The spec noted `source_used: "web"` in DAG nodes, whereas the tool artifact declared `source_used="web_search"`.
- **Root Cause:** Discrepancy between historical state labels in the 10-node DAG and canonical tool artifact identifiers.
- **Resolution:** Workflows align directly on `artifact.source_used` for full dynamic attribution.

---

## 5. Codified Architectural Guardrails

1. **Single Source of Truth for Evidence**:
   - Formatting, citation parsing, and context generation must remain encapsulated inside `app/rag/tools/`. Graph nodes and workflows must never implement ad-hoc regex scraping on tool output strings.
2. **Strict Zero-Shim Purity**:
   - Domain Pydantic models must expose only typed attributes. Never implement `__getitem__` or `.get()` on domain models to emulate dictionaries.
3. **Pure Native Config Injection**:
   - Never use global `ContextVar` instances or synthetic call IDs to pass runtime parameters (`RunnableConfig`) into tools. Rely exclusively on native framework parameter injection.
4. **Always Prefer Canonical Test Doubles**:
   - When unit testing workflows that invoke tools, use `FakeRAGTool` from `app/rag/tools/` instead of un-specced `MagicMock` instances.
