# Technical Specification: Deepen Tool Abstraction and Evidence Seam

- **ADR Reference:** `docs/adr/0024-deepen-tool-abstraction-and-evidence-seam.md` (supersedes ADR-0008)
- **Target Subsystems:** `app/rag/tools/`, `app/rag/engine.py`, `app/rag/workflows/react_agent.py`, `app/rag/workflows/self_corrective.py`, `app/rag/workflows/listeners.py`, `tests/rag/`
- **Specification Document:** `docs/spec.md` & `docs/specs/0024-deepen-tool-abstraction-and-evidence-seam-spec.md`
- **Status:** Proposed / Ready for Review
- **Triage Label:** `ready-for-agent`

---

## Problem Statement

In ADR 0008, the unified `BaseTool` abstraction was introduced with `response_format="content_and_artifact"` to bridge fixed DAG workflows and autonomous ReAct agents. While this established common tool classes, three major architectural friction points compromise testability, type safety, and extensibility:

1. **Heterogeneous, Untyped Dictionary Artifacts & Regex Fallbacks**:
   Tools currently return ad-hoc, heterogeneous dictionaries (`{"documents": ..., "citations": ...}` in retriever vs `{"citations": ..., "retrieved_contexts": ...}` in web search). In `ReActAgentWorkflow.to_workflow_output()`, checking `isinstance(artifact, list)` fails on every run because the artifact is a dictionary. Consequently, the workflow silently falls back to scraping tool message strings with fragile regular expressions (`re.findall(r"\[Source:\s*([^\]]+)\]", ...)` and `re.findall(r"Title:\s*([^\n]+)\nURL:\s*([^\n]+)", ...)`).
2. **Global State & Monkey-Patching for Config Injection**:
   `DualOutputStructuredTool` intercepts invocations to store `RunnableConfig` in a global `ContextVar` (`_TOOL_INVOKE_CONFIG`), passes synthetic `tool_call_id="__direct__"` into `super().run()`, and inspects resulting `ToolMessage` instances to return `(content, artifact)`. Individual tools then call `get_invoke_config()` to recover the config. Modern LangChain supports native `RunnableConfig` injection directly into tool functions without global state or synthetic call IDs.
3. **Tight Workflow Coupling to Tool Names**:
   `ReActAgentWorkflow` hardcodes specific tool names (`"knowledge_base_search"`, `"web_search"`) to resolve `source_used`. Adding a new tool (e.g. Jira, Confluence, GitHub) requires modifying workflow internals. Similarly, `app/rag/engine.py` maintains manual citation deduplication loops in generation nodes.
4. **Tuple Duck-Typing at Invocation Seams**:
   Fixed DAG nodes in `app/rag/engine.py` require a defensive `_unpack_tool_output` helper to inspect whether raw output is a tuple, an object with an `.artifact` attribute, or a raw string, obscuring the tool seam interface.

---

## Solution

Deepen `app/rag/tools/` into a self-contained, deep module with first-class domain models, an explicit `RAGTool` execution seam, pure LangChain config injection, and a polymorphic evidence aggregator:

1. **Canonical Domain Models (`app/rag/tools/base.py`)**:
   - `Citation(BaseModel)`: Strongly-typed source attribution (`title: str`, `url: str = ""`, `type: str = "general"`).
   - `ToolEvidenceArtifact(BaseModel)`: Unified domain artifact returned by all tools:
     - `citations: list[Citation]`
     - `retrieved_contexts: list[str]`
     - `source_used: str` (self-describing evidence identifier)
     - `raw_payload: Any = None` (optional underlying provider documents/payload)
     - Implements dict-like subscripting fallback (`__getitem__`, `get`) for backward compatibility with legacy tests.
   - `ToolResult(BaseModel)`: High-leverage execution result for direct DAG callers:
     - `content: str`
     - `artifact: ToolEvidenceArtifact`
     - Implements `__iter__` to yield `(self.content, self.artifact)` for ergonomic 2-tuple unpacking.
2. **Deep `RAGTool` Seam & Zero-Shim Execution**:
   - Introduce `RAGTool(StructuredTool)` with explicit direct execution entrypoints:
     - `async def aexecute(self, input_args: dict[str, Any] | BaseModel | str, config: RunnableConfig | None = None) -> ToolResult`
     - `def execute(self, input_args: dict[str, Any] | BaseModel | str, config: RunnableConfig | None = None) -> ToolResult`
   - For autonomous ReAct agents (`create_agent` / LangGraph `ToolNode`), standard `.ainvoke()` produces a `ToolMessage` with `ToolEvidenceArtifact` on `.artifact`.
   - Tool functions declare `config: RunnableConfig | None = None` and receive runtime configs via native LangChain injection.
   - Prune obsolete symbols: remove `DualOutputStructuredTool`, `_TOOL_INVOKE_CONFIG`, `_UNSET`, and `get_invoke_config()`.
3. **Polymorphic Evidence Aggregator (`aggregate_tool_evidence`)**:
   - Provide `aggregate_tool_evidence(messages_or_artifacts: Sequence[Any]) -> AggregatedEvidence` in `app/rag/tools/`.
   - Automatically extracts `ToolEvidenceArtifact` instances from message history, deduplicates citations by `(title, url)`, aggregates contexts, and dynamically resolves `source_used`:
     - 0 tools executed &rarr; `"direct"`
     - 1 tool executed &rarr; tool artifact's `source_used`
     - 2+ distinct tool sources &rarr; `"react_agent"`
   - Eliminate all regex scraping fallbacks and tool name checks from `ReActAgentWorkflow.to_workflow_output()`.
4. **Fixed DAG Node Simplification (`app/rag/engine.py`)**:
   - Update `node_retrieve_kb` and `node_search_web` to invoke `await tool.aexecute(..., config=config)`.
   - Directly extract `result.content`, `result.artifact.citations`, and `result.artifact.retrieved_contexts`.
   - Delete `_unpack_tool_output`.
5. **Deterministic Test Double (`FakeRAGTool`)**:
   - Provide an in-memory `FakeRAGTool(RAGTool)` test double with configurable returns and call inspection for deterministic unit tests.

---

## User Stories

1. As a developer adding a new retrieval tool (e.g., Jira or Confluence), I want to return a typed `ToolEvidenceArtifact` with a self-describing `source_used`, so that all existing workflows and evaluation listeners automatically support my tool without modifying workflow routing code.
2. As a workflow developer, I want `tool.aexecute(input, config=config)` to return a typed `ToolResult`, so that I do not have to write duck-typing unpack helpers or handle synthetic tool call IDs.
3. As a maintainer of `ReActAgentWorkflow`, I want evidence citations and retrieved contexts to be extracted directly from `ToolEvidenceArtifact`, so that the agent never relies on brittle regex parsing of tool string prose.
4. As an engineer writing tests for tool invocations, I want `ToolResult` to support 2-tuple unpacking `(content, artifact) = result`, so that existing test assertions remain clean and expressive.
5. As an engineer configuring Langfuse tracing, I want runtime `RunnableConfig` to be injected into tool implementations natively by LangChain, so that global `ContextVar` monkey-patches and thread-safety hazards are eliminated.
6. As a developer debugging evaluation pipelines, I want all citations across all tools to have a standard `Citation` schema (`title`, `url`, `type`), so that UI citation badges and evaluation metrics do not encounter inconsistent dictionary keys.
7. As an engineer writing unit tests for workflows, I want a `FakeRAGTool` test double, so that I can mock tool executions deterministically without setting up complex LangChain mock structures or external network calls.
8. As a frontend client consuming chat responses, I want citations generated by autonomous ReAct agents to match citations generated by fixed DAG workflows, so that citation display is consistent regardless of execution workflow.
9. As an engineer monitoring system performance, I want `node_retrieve_kb` and `node_search_web` to natively await `aexecute`, so that threadpools are not blocked during streaming token generation.
10. As a code reviewer, I want zero obsolete shims (`DualOutputStructuredTool`, `_TOOL_INVOKE_CONFIG`) remaining in `app/rag/tools/`, so that the codebase adheres strictly to the repo's Zero-Shim architecture standard.

---

## Implementation Decisions

1. **`Citation` Value Object (`app/rag/tools/base.py`)**:
   ```python
   class Citation(BaseModel):
       """Strongly-typed reference citation attributed to retrieved evidence."""
       title: str
       url: str = ""
       type: str = "general"
   ```
2. **`ToolEvidenceArtifact` Domain Model (`app/rag/tools/base.py`)**:
   ```python
   class ToolEvidenceArtifact(BaseModel):
       """Canonical structured evidence payload returned alongside tool display text."""
       citations: list[Citation] = Field(default_factory=list)
       retrieved_contexts: list[str] = Field(default_factory=list)
       source_used: str = "direct"
       raw_payload: Any = None

       def __getitem__(self, key: str) -> Any:
           return getattr(self, key)

       def get(self, key: str, default: Any = None) -> Any:
           return getattr(self, key, default)
   ```
3. **`ToolResult` Container (`app/rag/tools/base.py`)**:
   ```python
   class ToolResult(BaseModel):
       """Execution result container for direct workflow callers."""
       model_config = ConfigDict(arbitrary_types_allowed=True)

       content: str
       artifact: ToolEvidenceArtifact

       def __iter__(self):
           return iter((self.content, self.artifact))
   ```
4. **`RAGTool` Class & Factory (`app/rag/tools/base.py`)**:
   - Subclasses `StructuredTool` from `langchain_core.tools`.
   - `aexecute`: converts input to dictionary (if `BaseModel` or `str`), runs `arun` with native `config`, and wraps the resulting `(content, artifact)` or `ToolMessage` into `ToolResult`.
   - `execute`: synchronous version of `aexecute`.
   - `rag_tool(func, coroutine, name, description, args_schema) -> RAGTool`: clean instantiation factory setting `response_format="content_and_artifact"`.
5. **Pruning Obsolete Symbols (Zero-Shim Standard)**:
   - Completely remove `DualOutputStructuredTool`, `_TOOL_INVOKE_CONFIG`, `_UNSET`, and `get_invoke_config()` from `app/rag/tools/base.py`.
   - In `app/rag/tools/retriever.py` and `app/rag/tools/search.py`:
     - Delete `get_invoke_config()` and ContextVar reads.
     - Accept `config: RunnableConfig | None = None` directly in `_run` and `_arun`.
     - Return `(content, ToolEvidenceArtifact(...))`.
6. **Centralized Polymorphic Evidence Aggregation (`app/rag/tools/base.py`)**:
   ```python
   class AggregatedEvidence(BaseModel):
       citations: list[Citation] = Field(default_factory=list)
       retrieved_contexts: list[str] = Field(default_factory=list)
       source_used: str = "direct"

   def aggregate_tool_evidence(messages_or_artifacts: Sequence[Any]) -> AggregatedEvidence:
       ...
   ```
   - Scans sequence for `ToolEvidenceArtifact` (or `ToolMessage.artifact`).
   - Deduplicates citations by unique `(title, url)`.
   - Collects all `retrieved_contexts` preserving first-seen order.
   - Resolves `source_used`: `"direct"` if empty, single source if uniform, `"react_agent"` if multiple distinct sources.
7. **Refactor `ReActAgentWorkflow` (`app/rag/workflows/react_agent.py`)**:
   - In `to_workflow_output`:
     - Replace regex scraping and hardcoded tool checks with `evidence = aggregate_tool_evidence(messages)`.
     - Assign `citations = [c.model_dump() for c in evidence.citations]`.
     - Assign `retrieved_contexts = evidence.retrieved_contexts`.
     - Assign `source_used = evidence.source_used`.
   - Delete obsolete `_extract_tools_called` and `_resolve_source_used`.
8. **Refactor `create_agentic_rag_graph` (`app/rag/engine.py`)**:
   - In `node_retrieve_kb`:
     ```python
     result = await active_kb_tool.aexecute({"query": search_query}, config=config)
     return {
         "kb_docs": result.artifact.raw_payload or [],
         "retrieved_contexts": result.artifact.retrieved_contexts,
         "citations": [c.model_dump() for c in result.artifact.citations],
         "trace": [f"Private KB retrieval → {len(result.artifact.retrieved_contexts)} chunks"],
     }
     ```
   - In `node_search_web`:
     ```python
     result = await active_web_tool.aexecute({"query": search_query}, config=config)
     return {
         "web_results": result.content,
         "citations": [c.model_dump() for c in result.artifact.citations],
         "source_used": "web",
         "retrieved_contexts": result.artifact.retrieved_contexts,
         "trace": ["Web fallback → Tavily search"],
     }
     ```
   - Delete `_unpack_tool_output`.
9. **`FakeRAGTool` Test Double (`app/rag/tools/base.py`)**:
   - Extends `RAGTool`. Allows injecting fixed or sequential `content` and `artifact`.
   - Records all `execute` and `aexecute` calls with inputs and configs.

---

## Testing Decisions

1. **Unit Testing External Seam Behavior**:
   - Test `RAGTool.execute` and `RAGTool.aexecute` across string, dict, and BaseModel inputs.
   - Verify `ToolResult` unpacks cleanly as `content, artifact = res`.
   - Verify `ToolEvidenceArtifact` maintains typed fields while supporting dictionary key lookups.
   - Verify `config: RunnableConfig` is received inside `_run` and `_arun` via native LangChain injection without `_TOOL_INVOKE_CONFIG`.
2. **Dynamic Aggregator Testing**:
   - Verify `aggregate_tool_evidence` with empty messages produces `source_used="direct"`.
   - Verify single tool produces tool-specific `source_used` (e.g. `"vectorstore"` or `"web_search"`).
   - Verify multiple distinct tools produce `source_used="react_agent"`.
   - Verify duplicate citations across multiple tool messages are deduplicated by `(title, url)`.
3. **Workflow Integration & Regression**:
   - Verify `ReActAgentWorkflow` extracts structured citations without regex scraping.
   - Verify `SelfCorrectiveRagWorkflow` runs end-to-end through `retrieve_kb` and `search_web`.
   - Run the complete project test suite (`pytest`) to ensure zero regressions across memory, streaming, telemetry, and evaluation.

---

## Out of Scope

1. Introducing new external search providers (e.g. Bing Search, Google Search) or new vector databases.
2. Modifying the 10-node DAG topology or LangGraph state machine structure in `app/rag/engine.py`.
3. Changing HTTP presentation route contracts (`/api/chat`, `/api/chat/stream`).

---

## Further Notes

- This specification adheres strictly to the Phase Gating Invariant: production implementation work begins only after this specification is reviewed and approved.
- All changes maintain 100% backward compatibility for API consumers, while internally transitioning from untyped dictionaries and regex scraping to strongly-typed domain models.
