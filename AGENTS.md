## Agent skills

### Issue tracker

GitHub issues (`amiralimiri/RAG-Engine`). See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`GLOSSARY.md` + `docs/adr/` at the repo root). See `docs/agents/domain.md`.

### Tool abstractions

Dual-output `BaseTool` seams and test doubles. See `docs/agents/tool-abstractions.md`.

### Knowledge store & indexing

VectorStoreAdapter protocol seam, deterministic chunk hashing, and offline test doubles. See `docs/agents/knowledge-store.md`.

### Workflow execution & pipeline

WorkflowRunner execution pipeline, dependency injection, and streaming guardrails. See `docs/agents/workflow-execution.md`.

### Codebase unification & zero-shim architecture

Zero-shim dependency injection standards, obsolete symbol pruning, and modern Pytest fixture architecture. See `docs/agents/codebase-unification.md`.

### Evaluation engine & evaluator seam

Unified Evaluator protocol seam, lazy judge lifecycles, execution context retrieval, and offline test doubles. See `docs/agents/evaluation-engine.md`.

### Conversation memory & session branching

ConversationMemoryAdapter protocol seam, SQLite persistent storage, atomic rewind/truncation, session branching, and offline test doubles. See `docs/agents/conversation-memory.md`.
