# 01: Canonical Domain Models and Polymorphic Evidence Aggregator

**What to build:**
Define the canonical domain models (`Citation`, `ToolEvidenceArtifact`, `ToolResult`, `AggregatedEvidence`) and the polymorphic evidence aggregator (`aggregate_tool_evidence`) in `app/rag/tools/base.py`. This establishes the single source of truth for citations, contexts, and source attribution, providing backward-compatible dictionary subscripting on `ToolEvidenceArtifact` and 2-tuple unpacking on `ToolResult`.

**Blocked by:**
None (can start immediately).

**Status:** ready-for-agent

## Acceptance Criteria

- [ ] `Citation` domain model implemented with `title: str`, `url: str = ""`, and `type: str = "general"`.
- [ ] `ToolEvidenceArtifact` domain model implemented with `citations: list[Citation]`, `retrieved_contexts: list[str]`, `source_used: str = "direct"`, and `raw_payload: Any = None`.
- [ ] `ToolEvidenceArtifact` implements `__getitem__` and `get` to support subscripting fallback for backward compatibility with legacy dictionary access.
- [ ] `ToolResult` container implemented with `content: str` and `artifact: ToolEvidenceArtifact`, implementing `__iter__` to yield `(self.content, self.artifact)` for 2-tuple unpacking.
- [ ] `AggregatedEvidence` model implemented with `citations: list[Citation]`, `retrieved_contexts: list[str]`, and `source_used: str`.
- [ ] `aggregate_tool_evidence(messages_or_artifacts: Sequence[Any]) -> AggregatedEvidence` implemented:
  - Extracts `ToolEvidenceArtifact` instances directly or from `ToolMessage.artifact`.
  - Deduplicates citations by unique `(title, url)`.
  - Preserves chronological order of `retrieved_contexts`.
  - Dynamically resolves `source_used`: `"direct"` when empty, single tool source when uniform, or `"react_agent"` when multiple distinct tool sources were invoked.
- [ ] Dedicated unit tests in `tests/rag/test_tools.py` verifying domain model validation, subscripting fallback, tuple unpacking, and dynamic evidence aggregation.
- [ ] Existing test suite remains green.
